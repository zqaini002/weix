/**
 * macOS Mach VM 内存扫描辅助程序。
 *
 * 绕过 Python ctypes + libffi 在 ARM64 macOS 上调用 mach_vm_region 时的
 * 调用约定不兼容问题，直接在 C 中完成内存区域枚举和密钥扫描。
 *
 * 编译: cc -O2 -o mach_helper mach_helper.c
 * 使用: sudo ./mach_helper <pid>
 * 输出: 每行一个候选密钥 (64 hex key + 32 hex salt)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <ctype.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>

#define MAX_REGIONS       20000
#define MAX_CHUNK_SIZE    (10 * 1024 * 1024)  // 10 MB
#define MIN_KEY_LEN       96                   // 64 hex key + 32 hex salt

// --- 十六进制验证 ---

static int is_hex_char(char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

// --- 在内存数据中扫描密钥模式 ---

static void scan_for_keys(const uint8_t *data, size_t len) {
    for (size_t i = 0; i + MIN_KEY_LEN <= len; i++) {
        // 检查前 96 个字符是否都是 hex
        int valid = 1;
        for (int j = 0; j < MIN_KEY_LEN; j++) {
            if (!is_hex_char(data[i + j])) {
                valid = 0;
                break;
            }
        }
        if (!valid) continue;

        // 检查周围字符 (边界)
        if (i > 0 && is_hex_char(data[i - 1])) continue;
        if (i + MIN_KEY_LEN < len && is_hex_char(data[i + MIN_KEY_LEN])) continue;

        // 输出密钥: 大写 hex
        for (int j = 0; j < MIN_KEY_LEN; j++) {
            putchar(toupper(data[i + j]));
        }
        putchar('\n');
        fflush(stdout);
    }
}

// --- 主函数 ---

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s <pid>\n", argv[0]);
        return 1;
    }

    int pid = atoi(argv[1]);
    if (pid <= 0) {
        fprintf(stderr, "Invalid PID: %s\n", argv[1]);
        return 1;
    }

    // 获取 task port
    mach_port_t task = MACH_PORT_NULL;
    kern_return_t kr = task_for_pid(mach_task_self(), pid, &task);
    if (kr != KERN_SUCCESS || task == MACH_PORT_NULL) {
        fprintf(stderr, "task_for_pid failed: kr=%d (need root?)\n", kr);
        return 2;
    }

    // 枚举内存区域并扫描
    mach_vm_address_t address = 0;
    int region_count = 0;
    int scanned_count = 0;

    while (region_count < MAX_REGIONS) {
        mach_vm_address_t region_addr = address;
        mach_vm_size_t region_size = 0;
        vm_region_basic_info_data_64_t info;
        mach_msg_type_number_t info_cnt = VM_REGION_BASIC_INFO_COUNT_64;
        mach_port_t object_name = MACH_PORT_NULL;

        kr = mach_vm_region(
            task,
            &region_addr,
            &region_size,
            VM_REGION_BASIC_INFO_64,
            (vm_region_info_t)&info,
            &info_cnt,
            &object_name
        );

        if (kr != KERN_SUCCESS) break;
        region_count++;

        // 只处理可读且大小合理的区域
        if (!(info.protection & VM_PROT_READ)) {
            address = region_addr + region_size;
            continue;
        }
        if (region_size == 0 || region_size > 500 * 1024 * 1024) {
            address = region_addr + region_size;
            continue;
        }

        // 读取并扫描区域内存
        uint8_t *buffer = (uint8_t *)malloc(MAX_CHUNK_SIZE);
        if (!buffer) {
            address = region_addr + region_size;
            continue;
        }

        mach_vm_address_t read_addr = region_addr;
        mach_vm_size_t remaining = region_size;

        while (remaining > 0) {
            mach_vm_size_t chunk = remaining;
            if (chunk > MAX_CHUNK_SIZE) chunk = MAX_CHUNK_SIZE;

            mach_vm_size_t out_size = 0;
            kr = mach_vm_read_overwrite(
                task,
                read_addr,
                chunk,
                (mach_vm_address_t)buffer,
                &out_size
            );

            if (kr == KERN_SUCCESS && out_size > 0) {
                scan_for_keys(buffer, out_size);
                scanned_count++;
            } else if (kr == KERN_INVALID_ADDRESS) {
                // 区域部分不可读，跳过
                break;
            }
            // 其他错误: 继续尝试

            read_addr += chunk;
            remaining -= chunk;
        }

        free(buffer);
        address = region_addr + region_size;
    }

    fprintf(stderr, "Scanned %d regions (%d chunks), found in %d regions\n",
            region_count, scanned_count, region_count);

    // 释放 task port
    if (task != MACH_PORT_NULL) {
        mach_port_deallocate(mach_task_self(), task);
    }

    return 0;
}
