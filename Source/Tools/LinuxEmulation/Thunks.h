// SPDX-License-Identifier: MIT
#pragma once

#include <FEXCore/Core/Thunks.h>
#include <FEXCore/fextl/memory.h>
#include <FEXCore/IR/IR.h>

#include <span>

namespace FEXCore::Core { struct InternalThreadState; }

namespace FEX::HLE {
struct ThreadStateObject;

class ThunkHandler : public FEXCore::ThunkHandler {
public:
  virtual void RegisterTLSState(FEX::HLE::ThreadStateObject* ThreadObject) = 0;
  /**
   * @brief Allows the frontend to register its own thunk handlers independent of what is controlled in the backend.
   *
   * @param CTX A valid non-null context instance.
   * @param Definitions A vector of thunk definitions that the frontend controls
   */
  virtual void AppendThunkDefinitions(std::span<const FEXCore::IR::ThunkDefinition> Definitions) = 0;
  virtual void BeginGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;
  virtual void CommitGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;
  virtual void RollbackGuestRangeRetirement(uintptr_t Base, uintptr_t Length) = 0;
};
fextl::unique_ptr<ThunkHandler> CreateThunkHandler();
} // namespace FEX::HLE
