from pathlib import Path

path = Path("unittests/ThunkLibs/generator.cpp")
text = path.read_text()
anchor = 'TEST_CASE_METHOD(Fixture, "VoidPointerParameter") {'
if text.count(anchor) != 1:
    raise SystemExit(f"expected one insertion anchor, found {text.count(anchor)}")

test = r'''
TEST_CASE_METHOD(Fixture, "Custom repack function-pointer member") {
  auto guest_abi = GENERATE(GuestABI::X86_32, GuestABI::X86_64);
  INFO(guest_abi);

  const char* prelude = "using callback = void (*)(void*);\n"
                        "struct A { callback fn; };\n";
  const char* code = "#include <thunks_common.h>\n"
                     "void func(const A*);\n"
                     "template<auto> struct fex_gen_config {};\n"
                     "template<> struct fex_gen_config<&A::fn> : fexgen::custom_repack {};\n"
                     "template<> struct fex_gen_config<func> {};\n";

  CHECK_NOTHROW(run_thunkgen_host(prelude, code, guest_abi));
}

TEST_CASE_METHOD(Fixture, "Custom repack allocator-like pointer members") {
  auto guest_abi = GENERATE(GuestABI::X86_32, GuestABI::X86_64);
  INFO(guest_abi);

  const char* prelude = "using cb0 = void* (*)(void*, unsigned long, unsigned long, unsigned);\n"
                        "using cb1 = void* (*)(void*, void*, unsigned long, unsigned long, unsigned);\n"
                        "using cb2 = void (*)(void*, void*);\n"
                        "using cb3 = void (*)(void*, unsigned long, unsigned, unsigned);\n"
                        "struct A { void* userdata; cb0 alloc; cb1 realloc; cb2 free; cb3 ia; cb3 iff; };\n";
  const char* code = "#include <thunks_common.h>\n"
                     "void func(const A*);\n"
                     "template<auto> struct fex_gen_config {};\n"
                     "template<> struct fex_gen_config<&A::userdata> : fexgen::custom_repack {};\n"
                     "template<> struct fex_gen_config<&A::alloc> : fexgen::custom_repack {};\n"
                     "template<> struct fex_gen_config<&A::realloc> : fexgen::custom_repack {};\n"
                     "template<> struct fex_gen_config<&A::free> : fexgen::custom_repack {};\n"
                     "template<> struct fex_gen_config<&A::ia> : fexgen::custom_repack {};\n"
                     "template<> struct fex_gen_config<&A::iff> : fexgen::custom_repack {};\n"
                     "template<> struct fex_gen_config<func> {};\n";

  CHECK_NOTHROW(run_thunkgen_host(prelude, code, guest_abi));
}

'''
path.write_text(text.replace(anchor, test + anchor, 1))
