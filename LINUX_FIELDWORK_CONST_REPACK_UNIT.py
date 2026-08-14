from pathlib import Path

path = Path('unittests/ThunkLibs/generator.cpp')
text = path.read_text()

anchor = '''  SECTION("Pointer to struct with consistent data layout") {
    CHECK_NOTHROW(run_thunkgen_host("struct A { int a; };\\n", code, guest_abi));
  }

'''
insert = anchor + '''  SECTION("Const pointer preserves pointee constness in repack wrapper") {
    const std::string const_code = "#include <thunks_common.h>\\n"
                                   "void const_func(const A*);\\n"
                                   "template<auto> struct fex_gen_config {};\\n"
                                   "template<> struct fex_gen_config<const_func> : fexgen::custom_host_impl {};\\n";
    const auto output = run_thunkgen_host("struct A { int a; };\\n", const_code, guest_abi);
    CHECK(output.code.find("make_repack_wrapper<const") != std::string::npos);
  }

'''
if text.count(anchor) != 1:
    raise SystemExit(f'const repack unit anchor count={text.count(anchor)}')
path.write_text(text.replace(anchor, insert, 1))
