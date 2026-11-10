#pragma once

#include <algorithm>
#include <cstdint>
#include <pqrs/json.hpp>
#include <vector>

namespace krbn {
// Exact input patterns only. Device scope is part of the physical input identity,
// independent of application, variable, and other manipulator conditions.
struct raw_record_definition final {
  uint32_t vendor_id;
  uint32_t product_id;
  uint32_t collection_usage_page;
  uint32_t collection_usage;
  uint32_t report_id;
  uint32_t payload_length;
  std::vector<uint8_t> key_down;
  std::vector<uint8_t> key_up;

  auto operator<=>(const raw_record_definition&) const = default;
};

inline uint32_t raw_record_unsigned(const nlohmann::json& value,
                                    uint32_t maximum) {
  if ((!value.is_number_unsigned() && !value.is_number_integer()) ||
      (value.is_number_integer() && value.get<int64_t>() < 0) ||
      value.get<uint64_t>() > maximum) {
    throw pqrs::json::unmarshal_error("raw_record requires an integer in the allowed range");
  }
  return value.get<uint32_t>();
}

inline void from_json(const nlohmann::json& json, raw_record_definition& value) {
  pqrs::json::requires_object(json, "raw_record");
  const std::vector<std::string> fields{
      "vendor_id", "product_id", "collection_usage_page", "collection_usage",
      "report_id", "payload_length", "key_down", "key_up"};
  for (const auto& key : fields) {
    if (!json.contains(key)) {
      throw pqrs::json::unmarshal_error("raw_record is missing " + key);
    }
  }
  for (const auto& [key, ignored] : json.items()) {
    if (std::find(fields.begin(), fields.end(), key) == fields.end()) {
      throw pqrs::json::unmarshal_error("unknown raw_record key: " + key);
    }
  }
  value.vendor_id = raw_record_unsigned(json.at("vendor_id"), 65535);
  value.product_id = raw_record_unsigned(json.at("product_id"), 65535);
  value.collection_usage_page = raw_record_unsigned(json.at("collection_usage_page"), 65535);
  value.collection_usage = raw_record_unsigned(json.at("collection_usage"), 65535);
  value.report_id = raw_record_unsigned(json.at("report_id"), 255);
  value.payload_length = raw_record_unsigned(json.at("payload_length"), 4096);
  if (value.payload_length == 0) {
    throw pqrs::json::unmarshal_error("raw_record payload_length must be positive");
  }
  auto bytes = [&](const char* key) {
    const auto& array = json.at(key);
    pqrs::json::requires_array(array, key);
    if (array.size() != value.payload_length) {
      throw pqrs::json::unmarshal_error("raw_record pattern length must equal payload_length");
    }
    std::vector<uint8_t> result;
    for (const auto& byte : array) {
      result.push_back(static_cast<uint8_t>(raw_record_unsigned(byte, 255)));
    }
    return result;
  };
  value.key_down = bytes("key_down");
  value.key_up = bytes("key_up");
  if (value.key_down == value.key_up) {
    throw pqrs::json::unmarshal_error("raw_record key_down and key_up must differ");
  }
}
} // namespace krbn
