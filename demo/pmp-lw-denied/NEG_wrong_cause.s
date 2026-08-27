.section .text.init,"ax"
test_start:
	.byte 0x37 # 0x80020137
	.byte 0x01
	.byte 0x02
	.byte 0x80
	.byte 0x13 # 0x02011113
	.byte 0x11
	.byte 0x01
	.byte 0x02
	.byte 0x13 # 0x02015113
	.byte 0x51
	.byte 0x01
	.byte 0x02
	.byte 0x83 # 0x00012083
	.byte 0x20
	.byte 0x01
	.zero 1
	.byte 0x67
	.byte 0x80
	.byte 0x01
	.zero 65517

.section .data0,"aw"
	.byte 0x51, 0x51, 0x51, 0x51, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
	.zero 65520
.data
check_data0:
	.byte 0x37, 0x01, 0x02, 0x80, 0x13, 0x11, 0x01, 0x02, 0x13, 0x51, 0x01, 0x02, 0x83, 0x20, 0x01, 0x00
	.byte 0x67, 0x80, 0x01, 0x00
.data
check_data1:
	.byte 0x51, 0x51, 0x51, 0x51

.data
.balign 8
initial_gpr_values:
	/* x1 */
	.dword 0x0
	/* x2 */
	.dword 0x0
final_gpr_values:
	/* x1 */
	.dword 0x51515151
	/* x2 */
	.dword 0x80020000

.text
.global preamble
.global rvtest_entry_point
preamble:
rvtest_entry_point:
	/* Set up exception/trap handler: anything unexpected fails the test.
	   mtvec's low 2 bits are the mode field (0 = Direct), so the target must
	   be 4-byte aligned or those bits silently truncate the real address —
	   found by tracing an ecall trap under Spike landing at the wrong label. */
	la x3, trap_vector
	csrw mtvec, x3
	/* PMP entry 0: deny *all* access to the 4KiB region at 0x80020000;
	   entry 1: permit everything else. PMP is first-match, so the
	   denying entry has to be entry 0 — reversed, the catch-all matches
	   first, nothing is ever denied, and the test would pass while
	   proving the opposite of what it claims. */
	li x31, 0x200081ff
	csrw pmpaddr0, x31
	li x31, -1
	csrw pmpaddr1, x31
	li x31, 0x1f98	/* [0]=L|NAPOT,no perms; [1]=NAPOT,RWX */
	csrw pmpcfg0, x31

	/* Load general purpose registers */
	la x3, initial_gpr_values
	ld x1, 0(x3)
	ld x2, 8(x3)
	/* Point the exit register at `finish`, then jump to the test */
	la x3, finish
	j test_start

.align 2
finish:
	/* --trap-is-pass: the tested instruction was required to trap.
	   Reaching `finish` means it didn't, so this is a failure — the
	   normal comparison below would otherwise pass, since a permitted
	   access produces exactly the state isla predicted. */
	j comparison_fail

	/* Check general purpose registers */
	la x8, final_gpr_values
	ld x3, 0(x8)
	bne x1, x3, comparison_fail
	ld x3, 8(x8)
	bne x2, x3, comparison_fail
	/* Check memory. Past this point every GPR's test-relevant value has
	   already been compared above, so x5-x7/x28-x29 are free to clobber. */
	li x5, 0x80000000
	la x6, check_data0
	li x7, 20
check_data_loop0:
	beqz x7, check_data_end0
	lbu x28, 0(x5)
	lbu x29, 0(x6)
	bne x28, x29, comparison_fail
	addi x5, x5, 1
	addi x6, x6, 1
	addi x7, x7, -1
	j check_data_loop0
check_data_end0:
	li x5, 0x80020000
	la x6, check_data1
	li x7, 4
check_data_loop1:
	beqz x7, check_data_end1
	lbu x28, 0(x5)
	lbu x29, 0(x6)
	bne x28, x29, comparison_fail
	addi x5, x5, 1
	addi x6, x6, 1
	addi x7, x7, -1
	j check_data_loop1
check_data_end1:
	/* Report pass via the raw tohost/fromhost protocol Spike's HTIF actually
	   polls for bare-metal ELFs (no proxy kernel): write a nonzero, odd 32-bit
	   value (LSB = 1 = terminate) to tohost, zero to tohost+4, and keep
	   re-writing — matches riscv-tests' own write_tohost: pattern exactly, and
	   the pass/fail *values* (1 / 3) match riscv-arch-test's own
	   RVMODEL_HALT_PASS/RVMODEL_HALT_FAIL byte-for-byte.
	   (a7=93/ecall, tried first, does *not* work here: that's the Linux
	   syscall-proxy convention, only active under `spike pk ...`, not plain
	   `spike <elf>` — confirmed by tracing the ecall trapping right back into
	   our own trap_vector instead of halting.) */
	li t5, 1
	j write_tohost

	.global comparison_fail
comparison_fail:
	/* Same protocol, TESTNUM shifted+ORed with 1 per riscv-tests' RVTEST_FAIL,
	   giving a nonzero odd failure code distinguishable from the pass value. */
	li t5, 1
	slli t5, t5, 1
	ori t5, t5, 1
	j write_tohost

.align 2
trap_vector:
	/* Trap-aware handler (--expect-trap-cause=7): the tested
	   instruction is expected to trap with exactly this mcause. A
	   match advances mepc past the trapping instruction and resumes
	   via mret; anything else (wrong cause, or this vector not being
	   hit at all) still fails via comparison_fail as before. x30/x31
	   are scratch here -- safe because the tested instruction, by
	   construction (it traps with no normal retirement), never
	   writes a checked GPR of its own for this vector to clobber. */
	csrr x30, mcause
	li x31, 7
	bne x30, x31, comparison_fail
	/* The trap must also have come from the code under test. mtvec is
	   armed by the preamble's *first* instruction, so every later
	   preamble step -- enabling FP/vector, programming PMP entries,
	   building a page table -- runs with this handler live. A fault
	   raised there that happens to carry the cause this test expects
	   would otherwise report success having never reached the
	   instruction under test: right cause, entirely wrong reason. The
	   negative controls do not catch it either, because they vary the
	   expected cause rather than the trap's origin.
	   Compared as >= test_start rather than == because a tested
	   sequence may trap at any of its instructions, not only the
	   first. */
	csrr x30, mepc
	la x31, test_start
	bltu x30, x31, comparison_fail
	/* --trap-is-pass: taking this trap IS the result being tested
	   (a denied access, a page fault), so report success here rather
	   than resuming. Resuming would run the ordinary final-state
	   comparison, which fails by construction: the faulting
	   instruction never wrote its destination register, so no
	   expected value can match. */
	li t5, 1
	j write_tohost


write_tohost:
	la t6, tohost
	sw t5, 0(t6)
	sw zero, 4(t6)
	j write_tohost

	/* tohost/fromhost as a real, backed section (riscv-arch-test's own
	   RVMODEL_DATA_SECTION, config/sail/.../rvmodel_macros.h) instead of a bare
	   unbacked linker symbol at a fixed address. Functionally identical under
	   Spike's default memory map (verified), but a real symbol is what stays
	   portable to targets that don't tolerate an address with no backing
	   section -- QEMU, Whisper, real hardware -- and matches ACT4's own tests
	   byte-for-byte, which is what actually matters for running generated
	   tests through ACT4's pipeline. .dword (8 bytes) even on rv32: Spike's
	   HTIF reads tohost as 64-bit on every target; the upper 32 bits are
	   simply unused on rv32, same as riscv-arch-test's own definition. */
.pushsection .tohost,"aw",@progbits
.align 8
.global tohost
.type tohost, @object
.size tohost, 8
tohost: .dword 0
.align 8
.global fromhost
.type fromhost, @object
.size fromhost, 8
fromhost: .dword 0
.popsection
