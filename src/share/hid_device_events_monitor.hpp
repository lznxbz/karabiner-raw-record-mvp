#pragma once

#include "device_properties.hpp"
#include "hid_report_only_events.hpp"
#include "hid_report_only_events/raw_record.hpp"
#include <chrono>
#include <functional>
#include <memory>
#include <nod/nod.hpp>
#include <pqrs/cf/cf_ptr.hpp>
#include <pqrs/cf/run_loop_thread.hpp>
#include <pqrs/dispatcher.hpp>
#include <pqrs/gsl.hpp>
#include <pqrs/osx/iokit_hid_device_events_monitor.hpp>
#include <pqrs/osx/iokit_hid_value.hpp>
#include <pqrs/osx/iokit_return.hpp>
#include <string>
#include <vector>

namespace krbn {
// Publishes the values exposed by IOHIDQueue and, when enabled, events recovered
// directly from raw input reports through one chronologically consistent stream.
class hid_device_events_monitor final : public pqrs::dispatcher::extra::dispatcher_client {
public:
  //
  // Signals (invoked from the shared dispatcher thread)
  //

  nod::signal<void()> started;
  nod::signal<void()> stopped;
  nod::signal<void(pqrs::not_null_shared_ptr_t<std::vector<pqrs::osx::iokit_hid_value>>)> values_arrived;
  nod::signal<void(const std::string&, pqrs::osx::iokit_return)> error_occurred;

  //
  // Methods
  //

  struct configuration final {
    bool enable_input_report_handler = true;
    std::function<void(uint32_t, std::span<const uint8_t>)> input_report_observer;
  };

  hid_device_events_monitor(const hid_device_events_monitor&) = delete;

  hid_device_events_monitor(
      std::weak_ptr<pqrs::dispatcher::dispatcher> weak_dispatcher,
      pqrs::not_null_shared_ptr_t<pqrs::cf::run_loop_thread> run_loop_thread,
      IOHIDDeviceRef device,
      const device_properties& device_properties,
      configuration configuration)
      : dispatcher_client(weak_dispatcher),
        weak_dispatcher_(weak_dispatcher),
        run_loop_thread_(run_loop_thread),
        device_(device),
        device_properties_(device_properties),
        configuration_(std::move(configuration)),
        last_time_stamp_(0) {
    create_monitor();
  }

private:
  void create_monitor() {
    auto& configuration = configuration_;
    auto& device_properties = device_properties_;
    auto device = *device_;
    pqrs::osx::iokit_hid_device_events_monitor::parameters parameters;
    input_report_handler_ = nullptr;

    const auto& identifiers = device_properties.get_device_identifiers();
    if (configuration.enable_input_report_handler &&
        hid_report_only_events::is_target_device(identifiers)) {
      // Reading and parsing the descriptor is unnecessary for all other devices.
      auto report_descriptor = find_report_descriptor(device);

      input_report_handler_ =
          hid_report_only_events::make_report_handler(
              identifiers,
              report_descriptor);
    }

    if (!raw_record_definitions_.empty()) {
      auto raw = std::make_shared<hid_report_only_events::raw_record>(raw_record_definitions_);
      if (input_report_handler_) {
        input_report_handler_ = std::make_shared<hid_report_only_events::combined_report_handler>(input_report_handler_, raw);
      } else {
        input_report_handler_ = raw;
      }
    }
    if (input_report_handler_) {
      parameters.input_report_filter_started = [handler = input_report_handler_] {
        handler->reset_filter_state();
      };
    }

    if (configuration.input_report_observer || input_report_handler_) {
      parameters.observe_input_reports = true;

      parameters.input_report_filter =
          [observer = configuration.input_report_observer,
           handler = input_report_handler_](auto report_id, auto report) {
            if (observer) {
              observer(report_id, report);
            }

            return handler && handler->should_accept_report(report_id, report);
          };
    }

    device_events_monitor_ =
        std::make_shared<pqrs::osx::iokit_hid_device_events_monitor>(
            weak_dispatcher_,
            run_loop_thread_,
            device,
            parameters);

    device_events_monitor_->started.connect([this] {
      if (input_report_handler_) {
        // The vendor monitor enqueues started before any input_report_arrived
        // signal from the newly opened device.
        input_report_handler_->reset();
      }

      started();
    });

    device_events_monitor_->stopped.connect([this] {
      stopped();
    });

    device_events_monitor_->input_values_arrived.connect([this](auto&& values) {
      auto hid_values = std::make_shared<std::vector<pqrs::osx::iokit_hid_value>>();
      hid_values->reserve(values->size());

      for (const auto& value : *values) {
        hid_values->emplace_back(*value);
      }

      input_values_arrived(hid_values);
    });

    device_events_monitor_->input_report_arrived.connect(
        [this](auto report_id, auto report, auto time_stamp) {
          if (!input_report_handler_) {
            return;
          }

          auto hid_values = std::make_shared<std::vector<pqrs::osx::iokit_hid_value>>(
              input_report_handler_->handle(
                  report_id,
                  report,
                  time_stamp));
          if (hid_values->empty()) {
            return;
          }

          input_values_arrived(hid_values);
        });

    device_events_monitor_->error_occurred.connect([this](auto&& message, auto&& result) {
      error_occurred(message, result);
    });
  }

public:
  ~hid_device_events_monitor() override {
    detach_from_dispatcher([this] {
      device_events_monitor_ = nullptr;
    });
  }

  // Dispatcher thread only. The old monitor is fully detached before publishing
  // new definitions; its queued reports cannot enter the new generation.
  void set_raw_record_definitions(const std::vector<raw_record_definition>& definitions) {
    const auto& ids = device_properties_.get_device_identifiers();
    auto selected = ids.get_is_virtual_device()
                        ? std::vector<raw_record_definition>{}
                        : hid_report_only_events::raw_record::select_definitions(
                              type_safe::get(ids.get_vendor_id()),
                              type_safe::get(ids.get_product_id()),
                              find_report_descriptor(*device_), definitions);
    if (selected == raw_record_definitions_) {
      return;
    }
    bool was_seized = seized();
    device_events_monitor_ = nullptr;
    // Emit the normal device-ungrabbed lifecycle event, releasing all old inputs.
    // This is needed even when the last raw definition is removed.
    if (was_seized) {
      stopped();
    }
    raw_record_definitions_ = std::move(selected);
    last_time_stamp_ = pqrs::osx::chrono::absolute_time_point(0);
    create_monitor();
    // device_grabber re-evaluates its readiness and ignore gates before restart.
  }

  [[nodiscard]] bool has_raw_record_definitions() const {
    return !raw_record_definitions_.empty();
  }

  void async_start(IOOptionBits open_options,
                   std::chrono::milliseconds open_timer_interval) {
    device_events_monitor_->async_start(open_options,
                                        open_timer_interval);
  }

  void async_stop() {
    device_events_monitor_->async_stop();
  }

  [[nodiscard]] bool seized() const {
    return device_events_monitor_ && device_events_monitor_->seized();
  }

private:
  void input_values_arrived(
      pqrs::not_null_shared_ptr_t<std::vector<pqrs::osx::iokit_hid_value>> hid_values) {
    normalize_time_stamps(*hid_values);
    values_arrived(hid_values);
  }

  [[nodiscard]] static std::vector<uint8_t> find_report_descriptor(IOHIDDeviceRef device) {
    std::vector<uint8_t> result;

    if (auto property = IOHIDDeviceGetProperty(device, CFSTR(kIOHIDReportDescriptorKey))) {
      if (CFGetTypeID(property) == CFDataGetTypeID()) {
        auto data = static_cast<CFDataRef>(property);
        auto length = CFDataGetLength(data);
        if (length > 0) {
          result.resize(static_cast<size_t>(length));
          CFDataGetBytes(data, CFRangeMake(0, length), result.data());
        }
      }
    }

    return result;
  }

  void normalize_time_stamps(
      std::vector<pqrs::osx::iokit_hid_value>& values) {
    for (auto& value : values) {
      // Some devices send events with a timestamp that is zero, fixed, or moves
      // backwards, possibly due to macOS input event handling.
      //
      // For example, Swiftpoint ProPoint sends pointing events with normal
      // timestamps, but consumer key-up events with a zero or fixed timestamp.
      // Correct backward movement so time-dependent features such as to_if_alone
      // continue to work correctly.
      //
      // The IOHIDQueue and raw-report callbacks can also arrive in either order,
      // even when their events originated in the same input report. Applying the
      // same correction to both sources keeps their combined stream monotonic.
      auto time_stamp = value.get_time_stamp();
      if (time_stamp < last_time_stamp_) {
        value.set_time_stamp(last_time_stamp_);
      } else {
        last_time_stamp_ = time_stamp;
      }
    }
  }

  std::weak_ptr<pqrs::dispatcher::dispatcher> weak_dispatcher_;
  pqrs::not_null_shared_ptr_t<pqrs::cf::run_loop_thread> run_loop_thread_;
  pqrs::cf::cf_ptr<IOHIDDeviceRef> device_;
  const device_properties device_properties_;
  const configuration configuration_;
  std::vector<raw_record_definition> raw_record_definitions_;
  std::shared_ptr<pqrs::osx::iokit_hid_device_events_monitor> device_events_monitor_;

  // should_accept_report and reset_filter_state access filter state from run_loop_thread, while
  // handle and reset access handler state from the shared dispatcher thread.
  std::shared_ptr<hid_report_only_events::report_handler> input_report_handler_;
  pqrs::osx::chrono::absolute_time_point last_time_stamp_;
};
} // namespace krbn
