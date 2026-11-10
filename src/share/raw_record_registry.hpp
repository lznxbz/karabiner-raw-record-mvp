#pragma once

#include "raw_record_definition.hpp"
#include <limits>
#include <map>
#include <mutex>
#include <pqrs/hid.hpp>

namespace krbn {
// This baseline has no other internal usage pages. Negative pages cannot collide
// with physical HID pages. Keep the allocation explicit for future upstream merges.
inline constexpr auto raw_record_usage_page = pqrs::hid::usage_page::value_t(-1);

class raw_record_registry final {
public:
  // IDs are process-local and never reused, including after a profile switch.
  // Parsing a rejected manipulator can reserve an ID, but cannot activate a handler.
  static uint32_t source_id(const raw_record_definition& definition) {
    static std::mutex mutex;
    static std::map<raw_record_definition, uint32_t> ids;
    std::lock_guard lock(mutex);
    if (auto it = ids.find(definition); it != ids.end()) {
      return it->second;
    }
    if (ids.size() >= static_cast<size_t>(std::numeric_limits<int32_t>::max())) {
      throw pqrs::json::unmarshal_error("raw_record source ID space exhausted");
    }
    auto id = static_cast<uint32_t>(ids.size() + 1);
    ids.emplace(definition, id);
    return id;
  }
};
} // namespace krbn
