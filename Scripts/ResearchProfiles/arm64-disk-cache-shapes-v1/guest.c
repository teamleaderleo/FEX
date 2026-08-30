typedef unsigned long u64;

struct timespec {
  long seconds;
  long nanoseconds;
};

static volatile u64 observed;

static long syscall3(long number, long first, long second, long third) {
  register long result __asm__("rax");
  register long arg0 __asm__("rdi") = first;
  register long arg1 __asm__("rsi") = second;
  register long arg2 __asm__("rdx") = third;
  register long call __asm__("rax") = number;
  __asm__ volatile("syscall"
                   : "=a"(result)
                   : "a"(call), "r"(arg0), "r"(arg1), "r"(arg2)
                   : "rcx", "r11", "memory");
  return result;
}

static __attribute__((noinline)) u64 mix_left(u64 value) {
  if (value & 1) {
    return (value << 7) ^ (value >> 3) ^ 0x9e3779b97f4a7c15UL;
  }
  return (value << 11) + (value >> 5) + 0xd1b54a32d192ed03UL;
}

static __attribute__((noinline)) u64 mix_right(u64 value) {
  if ((value & 7) == 3) {
    return value * 0x94d049bb133111ebUL + 17;
  }
  if (value & 0x40) {
    return (value ^ 0xbf58476d1ce4e5b9UL) - (value >> 13);
  }
  return (value + 0x632be59bd9b4e019UL) ^ (value << 9);
}

static __attribute__((noinline)) u64 branchy(u64 value, u64 iteration) {
  switch ((value ^ iteration) & 3) {
  case 0:
    return mix_left(value + iteration);
  case 1:
    return mix_right(value ^ iteration);
  case 2:
    return mix_left(mix_right(value));
  default:
    return mix_right(mix_left(value));
  }
}

void _start(void) {
  static const char marker[] = "FEX_DISK_CACHE_GUEST_OK\n";
  struct timespec pause = {5, 0};
  u64 value = 0x123456789abcdef0UL;
  for (u64 iteration = 0; iteration != 128; ++iteration) {
    value = branchy(value, iteration);
  }
  observed = value;
  syscall3(1, 1, (long)marker, sizeof(marker) - 1);
  syscall3(35, (long)&pause, 0, 0);
  syscall3(60, 0, 0, 0);
  for (;;) {
  }
}
