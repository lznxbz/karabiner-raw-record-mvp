#pragma once

#include "hid_report_only_events/report_handler.hpp"
#include "raw_record_registry.hpp"
#include <algorithm>
#include <memory>

namespace krbn::hid_report_only_events {
class raw_record final : public report_handler {
public:
  // Layout validation uses the descriptor, including collections and complete
  // report length. An invalid or unexpected layout leaves the input inactive.
  static std::vector<raw_record_definition> select_definitions(
      uint64_t vendor_id, uint64_t product_id,
      std::span<const uint8_t> descriptor,
      const std::vector<raw_record_definition>& definitions) {
    std::vector<raw_record_definition> result;
    if (std::none_of(definitions.begin(), definitions.end(), [&](const auto& d) {
          return d.vendor_id == vendor_id && d.product_id == product_id;
        })) {
      return result;
    }
    auto parsed = pqrs::hid::report_descriptor::parse(descriptor);
    if (!parsed) {
      return result;
    }
    for (const auto& d : definitions) {
      if (d.vendor_id != vendor_id || d.product_id != product_id) {
        continue;
      }
      bool collection_found = false;
      uint64_t bit_length = 0;
      for (const auto& field : parsed->get_descriptor().get_report_fields()) {
        if (field.get_report_type() != pqrs::hid::report_descriptor::report_type::input ||
            type_safe::get(field.get_report_id()) != d.report_id) {
          continue;
        }
        auto offset = static_cast<uint64_t>(field.get_bit_offset());
        auto size = static_cast<uint64_t>(field.get_size_bits());
        auto count = static_cast<uint64_t>(field.get_count());
        constexpr uint64_t maximum_bits = 4096 * 8;
        if (offset > maximum_bits || size > maximum_bits ||
            (size != 0 && count > (maximum_bits - offset) / size)) {
          bit_length = maximum_bits + 8;
          break;
        }
        bit_length = std::max(bit_length, offset + size * count);
        const auto& path = field.get_collection_path();
        if (!path.empty() && path.front().get_usage()) {
          const auto& usage = *path.front().get_usage();
          if (type_safe::get(usage.get_usage_page()) == d.collection_usage_page &&
              type_safe::get(usage.get_usage()) == d.collection_usage) {
            collection_found = true;
          }
        }
      }
      if (collection_found && (bit_length + 7) / 8 == d.payload_length &&
          std::find(result.begin(), result.end(), d) == result.end()) {
        result.push_back(d);
      }
    }
    std::sort(result.begin(), result.end());
    return result;
  }

  explicit raw_record(std::vector<raw_record_definition> definitions)
      : definitions_(std::move(definitions)), pressed_(definitions_.size(), false) {
  }

  [[nodiscard]] bool should_accept_report(uint32_t report_id,
                                          std::span<const uint8_t> report) override {
    // Immutable definitions are the only state accessed from the run loop.
    return std::any_of(definitions_.begin(), definitions_.end(), [&](const auto& d) {
      auto payload = normalize(d, report_id, report);
      return payload && (std::ranges::equal(*payload, d.key_down) ||
                         std::ranges::equal(*payload, d.key_up));
    });
  }

  [[nodiscard]] std::vector<pqrs::osx::iokit_hid_value> handle(
      uint32_t report_id, std::span<const uint8_t> report,
      pqrs::osx::chrono::absolute_time_point time_stamp) override {
    std::vector<pqrs::osx::iokit_hid_value> result;
    for (size_t i = 0; i < definitions_.size(); ++i) {
      const auto& d = definitions_[i];
      auto payload = normalize(d, report_id, report);
      if (!payload) {
        continue;
      }
      bool down = std::ranges::equal(*payload, d.key_down);
      bool up = std::ranges::equal(*payload, d.key_up);
      if ((down || up) && down != pressed_[i]) {
        result.emplace_back(time_stamp, down ? 1 : 0, raw_record_usage_page,
                            pqrs::hid::usage::value_t(raw_record_registry::source_id(d)), 1, 0);
        pressed_[i] = down;
      }
    }
    return result;
  }

  void reset() override {
    std::fill(pressed_.begin(), pressed_.end(), false);
  }

private:
  static std::optional<std::span<const uint8_t>> normalize(
      const raw_record_definition& d, uint32_t report_id,
      std::span<const uint8_t> report) {
    if (report_id != d.report_id) {
      return std::nullopt;
    }
    // macOS includes a prefix for descriptor-declared nonzero report IDs.
    // An unnumbered descriptor has no prefix: its first payload byte is preserved.
    size_t prefix = d.report_id == 0 ? 0 : 1;
    if (report.size() != d.payload_length + prefix ||
        (prefix && report.front() != d.report_id)) {
      return std::nullopt;
    }
    return report.subspan(prefix);
  }

  const std::vector<raw_record_definition> definitions_;
  std::vector<bool> pressed_;
};

// When vendor and configured handlers coexist, execute all child filtering on
// the dispatcher. This avoids sharing mutable vendor filter state across threads.
class combined_report_handler final : public report_handler {
public:
  combined_report_handler(std::shared_ptr<report_handler> vendor,
                          std::shared_ptr<report_handler> raw)
      : handlers_{std::move(vendor), std::move(raw)} {
  }
  bool should_accept_report(uint32_t, std::span<const uint8_t>) override {
    return true;
  }
  std::vector<pqrs::osx::iokit_hid_value> handle(
      uint32_t id, std::span<const uint8_t> report,
      pqrs::osx::chrono::absolute_time_point time) override {
    std::vector<pqrs::osx::iokit_hid_value> result;
    for (const auto& handler : handlers_) {
      if (handler->should_accept_report(id, report)) {
        auto values = handler->handle(id, report, time);
        result.insert(result.end(), values.begin(), values.end());
      }
    }
    return result;
  }
  void reset() override {
    for (const auto& handler : handlers_) {
      handler->reset_filter_state();
      handler->reset();
    }
  }

private:
  const std::vector<std::shared_ptr<report_handler>> handlers_;
};
} // namespace krbn::hid_report_only_events
