#include "constants.hpp"
#include <cassert>
#include <iostream>
#include <limits>

int main() {
  using krbn::constants;
  assert(constants::get_tmp_directory() == "/Library/Application Support/org.pqrs.rawmvp/tmp");
  assert(constants::get_system_configuration_directory() == "/Library/Application Support/org.pqrs.rawmvp/config");
  assert(constants::get_user_configuration_directory().filename() == "karabiner-raw-record-mvp");
  assert(constants::get_user_data_directory().filename() == "karabiner-raw-record-mvp");
  assert(constants::get_user_core_configuration_file_path().parent_path() == constants::get_user_configuration_directory());
  assert(constants::get_karabiner_core_service_daemon_socket_file_path().string().size() <= 103);
  assert(constants::get_console_user_id_changed_receiver_socket_file_path().string().size() <= 103);
  assert(constants::get_console_user_server_socket_file_path(std::numeric_limits<uid_t>::max()).string().size() <= 103);
  std::cout << "Isolated configuration and IPC paths verified (8 assertions).\n";
}
