from pathlib import Path

path = Path("ThunkLibs/Generator/gen.cpp")
text = path.read_text()

old = '''      auto get_type_name_with_nonconst_pointee = [&](clang::QualType type) {
        type = type.getLocalUnqualifiedType();
        if (type->isPointerType()) {
          // Strip away "const" from pointee type
          type = context.getPointerType(type->getPointeeType().getLocalUnqualifiedType());
        }
        return get_type_name(context, type.getTypePtr());
      };
'''
new = '''      auto get_repack_wrapper_type_name = [&](clang::QualType type) {
        // Preserve pointee cv-qualification in the wrapper type. repack_wrapper
        // already removes cv for its internal host storage, while its template
        // type is also used to decide whether exit repacking may write back to
        // guest memory.
        type = type.getLocalUnqualifiedType();
        return get_type_name(context, type.getTypePtr());
      };
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one repack type helper, found {text.count(old)}")
text = text.replace(old, new, 1)

old_use = '''                     get_type_name_with_nonconst_pointee(param_type), param_idx);'''
new_use = '''                     get_repack_wrapper_type_name(param_type), param_idx);'''
if text.count(old_use) != 1:
    raise SystemExit(f"expected one repack helper use, found {text.count(old_use)}")
text = text.replace(old_use, new_use, 1)

path.write_text(text)
