#include "dispatcher_utility.hpp"
#include "event_queue.hpp"
#include "hid_report_only_events/raw_record.hpp"
#include "manipulator/manipulator_factory.hpp"
#include "manipulator/manipulator_manager.hpp"
#include "manipulator/manipulators/post_event_to_virtual_devices/post_event_to_virtual_devices.hpp"
#include "run_loop_thread_utility.hpp"
#include <boost/ut.hpp>
#include <thread>

namespace {
using namespace boost::ut;
using json = nlohmann::json;
using handler = krbn::hid_report_only_events::raw_record;
const auto stamp = pqrs::osx::chrono::absolute_time_point(12345);

json definition_json() {
  return {{"vendor_id", 25098}, {"product_id", 1031}, {"collection_usage_page", 1}, {"collection_usage", 128}, {"report_id", 3}, {"payload_length", 1}, {"key_down", {1}}, {"key_up", {0}}};
}

std::vector<uint8_t> descriptor(bool numbered = true) {
  // System Control, Power/Sleep/Wake bits and five bits of constant padding.
  std::vector<uint8_t> d{0x05, 0x01, 0x09, 0x80, 0xa1, 0x01};
  if (numbered) {
    d.insert(d.end(), {0x85, 0x03});
  }
  d.insert(d.end(), {0x75, 0x01, 0x95, 0x03, 0x15, 0x00, 0x25, 0x01,
                     0x19, 0x81, 0x29, 0x83, 0x81, 0x02,
                     0x95, 0x05, 0x81, 0x01, 0xc0});
  return d;
}

json rule_json() {
  return {{"type", "basic"}, {"from", {{"raw_record", definition_json()}}}, {"to", json::array({{{"key_code", "f13"}, {"repeat", false}}})}};
}

auto make_basic(const json& j) {
  return krbn::manipulator::manipulator_factory::make_manipulator(
      j, std::make_shared<krbn::core_configuration::details::complex_modifications_parameters>());
}

void tests() {
  "exact descriptor and device selection"_test = [] {
    auto d = definition_json().get<krbn::raw_record_definition>();
    expect(handler::select_definitions(25098, 1031, descriptor(), {d, d}).size() == 1u);
    expect(handler::select_definitions(6421, 4133, descriptor(), {d}).empty());
    expect(handler::select_definitions(25098, 1031, std::vector<uint8_t>{}, {d}).empty());
    auto wrong = d;
    wrong.collection_usage = 129;
    expect(handler::select_definitions(25098, 1031, descriptor(), {wrong}).empty());
    wrong = d;
    wrong.report_id = 2;
    expect(handler::select_definitions(25098, 1031, descriptor(), {wrong}).empty());
    wrong = d;
    wrong.payload_length = 2;
    expect(handler::select_definitions(25098, 1031, descriptor(), {wrong}).empty());
  };

  "Bluetooth multi-collection descriptor selects the correct report"_test = [] {
    // Public protocol data only: no address, serial number, or input recording.
    const std::string hex = "05010906a1018501050719e029e71500250175019508810295017508810195037501050819012903910295057501910195067508150026ff00050719002aff008100c0050c0901a1018502751095011500268c0219002a8c028100c005010980a1018503750195031500250119812983810295058101c005010902a10185040901a100050919012902150025017501950281029506810305010930093109381581257f750895038106c0c00600ff0900a101a102855a09001580257f750895ff8122c0c0";
    std::vector<uint8_t> bytes;
    for (size_t i = 0; i < hex.size(); i += 2) {
      bytes.push_back(static_cast<uint8_t>(std::stoul(hex.substr(i, 2), nullptr, 16)));
    }
    auto d = definition_json().get<krbn::raw_record_definition>();
    expect(handler::select_definitions(25098, 1031, bytes, {d}).size() == 1u);
    d.collection_usage = 6;
    expect(handler::select_definitions(25098, 1031, bytes, {d}).empty());
  };

  "diagnostic roundtrip and unmatched input never reach virtual HID"_test = [] {
    auto d = definition_json().get<krbn::raw_record_definition>();
    krbn::momentary_switch_event event(krbn::raw_record_usage_page,
                                       pqrs::hid::usage::value_t(krbn::raw_record_registry::source_id(d)));
    expect(json(event).get<krbn::momentary_switch_event>() == event);
    auto input = std::make_shared<krbn::event_queue::queue>();
    auto output = std::make_shared<krbn::event_queue::queue>();
    auto config = std::make_shared<krbn::core_configuration::core_configuration>();
    auto post = std::make_shared<krbn::manipulator::manipulators::post_event_to_virtual_devices::post_event_to_virtual_devices>(
        std::weak_ptr<krbn::console_user_server_peer>{}, std::weak_ptr<krbn::notification_message_manager>{});
    krbn::manipulator::manipulator_manager manager;
    manager.push_back_manipulator(post);
    for (auto type : {krbn::event_type::key_down, krbn::event_type::key_up}) {
      krbn::event_queue::event value(event);
      input->push_back_entry(krbn::event_queue::entry(krbn::device_id(1),
                                                      krbn::event_queue::event_time_stamp(stamp), value, type, std::nullopt,
                                                      value, krbn::event_queue::state::original));
      while (manager.manipulate(input, output, stamp, config)) {}
    }
    expect(output->empty());
    expect(post->get_queue().empty());
  };

  "composition preserves vendor reports and resets both handlers"_test = [] {
    class vendor_handler final : public krbn::hid_report_only_events::report_handler {
    public:
      int resets = 0;
      int filter_resets = 0;
      bool should_accept_report(uint32_t id, std::span<const uint8_t>) override { return id == 2; }
      std::vector<pqrs::osx::iokit_hid_value> handle(uint32_t, std::span<const uint8_t>,
                                                     pqrs::osx::chrono::absolute_time_point time) override {
        return {{time, 1, pqrs::hid::usage_page::button, pqrs::hid::usage::value_t(6), 1, 0}};
      }
      void reset() override { ++resets; }
      void reset_filter_state() override { ++filter_resets; }
    };
    auto vendor = std::make_shared<vendor_handler>();
    auto raw = std::make_shared<handler>(std::vector{definition_json().get<krbn::raw_record_definition>()});
    krbn::hid_report_only_events::combined_report_handler combined(vendor, raw);
    combined.reset();
    expect(vendor->resets == 1);
    expect(vendor->filter_resets == 1);
    auto normal = combined.handle(2, std::vector<uint8_t>{2, 1}, stamp);
    expect(normal.size() == 1u);
    expect(normal[0].get_usage_page() == pqrs::hid::usage_page::button);
    auto recovered = combined.handle(3, std::vector<uint8_t>{3, 1}, stamp);
    expect(recovered.size() == 1u);
    expect(recovered[0].get_usage_page() == krbn::raw_record_usage_page);
    expect(combined.handle(3, std::vector<uint8_t>{3, 1}, stamp).empty());
    combined.reset();
    expect(combined.handle(3, std::vector<uint8_t>{3, 1}, stamp).size() == 1u);
  };

  "strict JSON validation"_test = [] {
    auto valid = definition_json();
    for (const auto& [key, ignored] : valid.items()) {
      auto j = valid;
      j.erase(key);
      expect(throws<pqrs::json::unmarshal_error>([&] { j.get<krbn::raw_record_definition>(); }));
    }
    std::vector<json> bad;
    for (auto v : {json(-1), json(256), json(3.0), json(true), json("3")}) {
      auto j = valid;
      j["report_id"] = v;
      bad.push_back(j);
    }
    for (auto v : {json::array({-1}), json::array({256}), json::array({1.0}), json::array(), json::array({1, 0})}) {
      auto j = valid;
      j["key_down"] = v;
      bad.push_back(j);
    }
    auto j = valid;
    j["key_up"] = j["key_down"];
    bad.push_back(j);
    j = valid;
    j["payload_length"] = 0;
    bad.push_back(j);
    j = valid;
    j["unexpected"] = 1;
    bad.push_back(j);
    for (const auto& entry : bad) {
      expect(throws<pqrs::json::unmarshal_error>([&] { entry.get<krbn::raw_record_definition>(); }));
    }
  };

  "press repeat release and independent device state"_test = [] {
    auto d = definition_json().get<krbn::raw_record_definition>();
    handler a({d}), b({d});
    std::vector<uint8_t> down{3, 1}, up{3, 0};
    expect(a.should_accept_report(3, down));
    expect(a.handle(3, up, stamp).empty());
    auto events = a.handle(3, down, stamp);
    expect(events.size() == 1u);
    expect(events.front().get_integer_value() == 1);
    expect(events.front().get_usage_page() == krbn::raw_record_usage_page);
    expect(a.handle(3, down, stamp).empty());
    expect(b.handle(3, down, stamp).size() == 1u);
    auto release = a.handle(3, up, stamp);
    expect(release.size() == 1u);
    expect(release.front().get_integer_value() == 0);
    expect(a.handle(3, up, stamp).empty());
    expect(a.handle(3, down, stamp).size() == 1u);
    a.reset();
    expect(a.handle(3, down, stamp).size() == 1u);
  };

  "normalization never guesses a prefix"_test = [] {
    auto d = definition_json().get<krbn::raw_record_definition>();
    handler numbered({d});
    expect(!numbered.should_accept_report(2, std::vector<uint8_t>{3, 1}));
    for (const auto& bytes : std::vector<std::vector<uint8_t>>{{}, {1}, {2, 1}, {3, 1, 0}, {3, 2}}) {
      expect(!numbered.should_accept_report(3, bytes));
      expect(numbered.handle(3, bytes, stamp).empty());
    }
    d.report_id = 0;
    expect(handler::select_definitions(25098, 1031, descriptor(false), {d}).size() == 1u);
    handler unnumbered({d});
    expect(unnumbered.should_accept_report(0, std::vector<uint8_t>{1}));
    expect(unnumbered.handle(0, std::vector<uint8_t>{1}, stamp).size() == 1u);
    expect(!unnumbered.should_accept_report(0, std::vector<uint8_t>{0, 1}));
  };

  "shared identities survive reload without reuse"_test = [] {
    auto d = definition_json().get<krbn::raw_record_definition>();
    auto id = krbn::raw_record_registry::source_id(d);
    expect(id == krbn::raw_record_registry::source_id(d));
    auto other = d;
    other.key_down = {2};
    expect(id != krbn::raw_record_registry::source_id(other));
    expect(id == krbn::raw_record_registry::source_id(d));
    auto a = make_basic(rule_json());
    auto j = rule_json();
    j["conditions"] = json::array({{{"type", "variable_if"}, {"name", "example"}, {"value", 1}}});
    auto b = make_basic(j);
    auto aa = dynamic_cast<krbn::manipulator::manipulators::basic::basic*>(pqrs::unwrap_not_null(a).get());
    auto bb = dynamic_cast<krbn::manipulator::manipulators::basic::basic*>(pqrs::unwrap_not_null(b).get());
    expect(*aa->get_from().get_event_definitions()[0].get_if<krbn::momentary_switch_event>() ==
           *bb->get_from().get_event_definitions()[0].get_if<krbn::momentary_switch_event>());
  };

  "input-only syntax rejects mixed inputs and passthrough outputs"_test = [] {
    for (const auto& key : {"key_code", "consumer_key_code", "any", "simultaneous", "integer_value"}) {
      auto j = rule_json();
      j["from"][key] = "a";
      expect(throws<pqrs::json::unmarshal_error>([&] { make_basic(j); }));
    }
    auto j = rule_json();
    j["to"] = json::array({{{"raw_record", definition_json()}}});
    expect(throws<pqrs::json::unmarshal_error>([&] { make_basic(j); }));
    j = rule_json();
    j["to"] = json::array({{{"raw_record_key", 1}}});
    expect(throws<pqrs::json::unmarshal_error>([&] { make_basic(j); }));
    for (const auto& key : {"to", "to_after_key_up", "to_if_alone"}) {
      j = rule_json();
      j[key] = json::array({{{"from_event", true}}});
      expect(throws<pqrs::json::unmarshal_error>([&] { make_basic(j); }));
    }
  };

  "raw adapter through existing basic emits one complete F13 click"_test = [] {
    auto d = definition_json().get<krbn::raw_record_definition>();
    handler raw({d});
    auto properties = std::make_shared<krbn::device_properties>(krbn::device_properties::initialization_parameters{
        .device_id = krbn::device_id(1), .vendor_id = pqrs::hid::vendor_id::value_t(25098), .product_id = pqrs::hid::product_id::value_t(1031), .is_keyboard = true});
    auto input = std::make_shared<krbn::event_queue::queue>();
    auto output = std::make_shared<krbn::event_queue::queue>();
    auto config = std::make_shared<krbn::core_configuration::core_configuration>();
    krbn::manipulator::manipulator_manager manager;
    manager.push_back_manipulator(make_basic(rule_json()));
    for (const auto& bytes : std::vector<std::vector<uint8_t>>{{3, 1}, {3, 1}, {3, 1}, {3, 0}, {3, 0}}) {
      auto values = raw.handle(3, bytes, stamp);
      for (const auto& entry : *krbn::event_queue::utility::make_entries(properties, values, {})) {
        input->push_back_entry(*entry);
      }
      while (manager.manipulate(input, output, stamp, config)) {}
    }
    size_t downs = 0, ups = 0;
    for (const auto& entry : output->get_entries()) {
      auto e = entry.get_event().get_if<krbn::momentary_switch_event>();
      if (e && e->get_usage_pair() == pqrs::hid::usage_pair(pqrs::hid::usage_page::keyboard_or_keypad,
                                                            pqrs::hid::usage::keyboard_or_keypad::keyboard_f13)) {
        if (entry.get_event_type() == krbn::event_type::key_down) ++downs;
        if (entry.get_event_type() == krbn::event_type::key_up) ++ups;
      }
    }
    expect(downs == 1u);
    expect(ups == 1u);
  };
}
} // namespace

int main() {
  auto dispatcher = krbn::dispatcher_utility::initialize_dispatchers();
  auto run_loop = krbn::run_loop_thread_utility::initialize_scoped_run_loop_thread_manager(
      pqrs::cf::run_loop_thread::failure_policy::abort);
  tests();
}
