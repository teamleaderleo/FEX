#!/usr/bin/env python3
from pathlib import Path

gen = Path("ThunkLibs/Generator/gen.cpp")
text = gen.read_text()
old_helper = '''      auto get_type_name_with_nonconst_pointee = [&](clang::QualType type) {
        type = type.getLocalUnqualifiedType();
        if (type->isPointerType()) {
          // Strip away "const" from pointee type
          type = context.getPointerType(type->getPointeeType().getLocalUnqualifiedType());
        }
        return get_type_name(context, type.getTypePtr());
      };


'''
if text.count(old_helper) != 1:
    raise SystemExit(f"expected one nonconst-pointee helper, found {text.count(old_helper)}")
text = text.replace(old_helper, "", 1)

old = '''          fmt::print(file, "  auto a_{} = make_repack_wrapper<{}>(args->a_{});\\n", param_idx,
                     get_type_name_with_nonconst_pointee(param_type), param_idx);
'''
new = '''          // Preserve pointee constness in the wrapper type. repack_wrapper strips
          // const only for its internal host-layout storage, while retaining the
          // original pointer type to decide whether exit repacking may write back
          // to guest memory.
          fmt::print(file, "  auto a_{} = make_repack_wrapper<{}>(args->a_{});\\n", param_idx,
                     get_type_name(context, param_type.getTypePtr()), param_idx);
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one repack-wrapper emission anchor, found {text.count(old)}")
gen.write_text(text.replace(old, new, 1))

unit = Path("unittests/ThunkLibs/generator.cpp")
text = unit.read_text()
anchor = '''    SECTION("Struct member annotated as custom_repack") {
      CHECK_NOTHROW(run_thunkgen_host("struct A { void* a; };\\n",
                                      code + "template<> struct fex_gen_config<&A::a> : fexgen::custom_repack {};\\n", guest_abi));
    }
'''
addition = anchor + '''

    SECTION("Const parameter preserves pointee constness in repack wrapper") {
      const std::string const_code = "#include <thunks_common.h>\\n"
                                     "void func(const A*);\\n"
                                     "template<auto> struct fex_gen_config {};\\n"
                                     "template<> struct fex_gen_config<&A::a> : fexgen::custom_repack {};\\n"
                                     "template<> struct fex_gen_config<func> {};\\n";
      const auto output = run_thunkgen_host("struct A { void* a; };\\n", const_code, guest_abi);
      const auto wrapper_begin = output.code.find("make_repack_wrapper<");
      REQUIRE(wrapper_begin != std::string::npos);
      const auto wrapper_end = output.code.find(">(args->", wrapper_begin);
      REQUIRE(wrapper_end != std::string::npos);
      const auto wrapper_type = output.code.substr(wrapper_begin, wrapper_end - wrapper_begin);
      CHECK(wrapper_type.find("A") != std::string::npos);
      CHECK(wrapper_type.find("const") != std::string::npos);
    }
'''
if text.count(anchor) != 1:
    raise SystemExit(f"expected one StructRepacking insertion anchor, found {text.count(anchor)}")
unit.write_text(text.replace(anchor, addition, 1))
