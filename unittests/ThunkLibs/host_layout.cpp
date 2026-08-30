#include <catch2/catch_all.hpp>

#include <Host.h>

#include <cstdint>

TEST_CASE("CharPointerHostToGuestConversionPreservesAddress") {
  char storage {};
  char* mutable_pointer = &storage;
  const char* const_pointer = &storage;

  const guest_layout<int8_t*> mutable_signed = to_guest(to_host_layout(mutable_pointer));
  const guest_layout<uint8_t*> mutable_unsigned = to_guest(to_host_layout(mutable_pointer));
  const guest_layout<const int8_t*> const_signed = to_guest(to_host_layout(const_pointer));
  const guest_layout<const uint8_t*> const_unsigned = to_guest(to_host_layout(const_pointer));

  CHECK(mutable_signed.data == reinterpret_cast<uintptr_t>(mutable_pointer));
  CHECK(mutable_unsigned.data == reinterpret_cast<uintptr_t>(mutable_pointer));
  CHECK(const_signed.data == reinterpret_cast<uintptr_t>(const_pointer));
  CHECK(const_unsigned.data == reinterpret_cast<uintptr_t>(const_pointer));
}
