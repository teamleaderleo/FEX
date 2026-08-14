from pathlib import Path

path = Path("unittests/ThunkLibs/generator.cpp")
text = path.read_text()
marker = 'TEST_CASE_METHOD(Fixture, "Trivial") {'
if text.count(marker) != 1:
    raise SystemExit(f"expected one Trivial test anchor, found {text.count(marker)}")

test = r'''
TEST_CASE_METHOD(Fixture, "Custom host symbol registry follows namespace metadata") {
  constexpr std::string_view prelude = R"(
namespace internal {
void ordinary();
void custom();
}
)";
  constexpr std::string_view code = R"(
#include <common/GeneratorInterface.h>
namespace internal {
template<auto>
struct fex_gen_config : fexgen::generate_guest_symtable, fexgen::indirect_guest_calls {};
template<>
struct fex_gen_config<custom> : fexgen::custom_host_impl {};
}
)";

  const auto output = run_thunkgen(prelude, code);
  const auto macro_start = output.host.code.find("#define FOREACH_internal_CUSTOM_HOST_SYMBOL(EXPAND)");
  REQUIRE(macro_start != std::string::npos);
  const auto macro_end = output.host.code.find("\n\n", macro_start);
  REQUIRE(macro_end != std::string::npos);
  const auto registry = std::string_view {output.host.code}.substr(macro_start, macro_end - macro_start);

  CHECK(registry.find("EXPAND(custom)") != std::string_view::npos);
  CHECK(registry.find("ordinary") == std::string_view::npos);
}

'''
text = text.replace(marker, test + marker, 1)
path.write_text(text)
