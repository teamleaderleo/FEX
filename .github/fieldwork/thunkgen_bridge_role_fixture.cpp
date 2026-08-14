#include <common/GeneratorInterface.h>

template<auto>
struct fex_gen_config {
  unsigned version = 1;
};

template<typename>
struct fex_gen_type {};

using FixtureCallback = int (*)(int);
int fixture_register_callback(FixtureCallback callback, int value);
template<>
struct fex_gen_config<fixture_register_callback> {};

using FixtureExplicitBridge = long (*)(long);
template<>
struct fex_gen_type<FixtureExplicitBridge> {};
