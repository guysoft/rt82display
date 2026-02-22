/**
 * Emscripten syscall stubs for wasm2c-generated QGIF encoder
 * 
 * These functions implement the file I/O syscalls that the WASM module imports.
 * The WASM operates on a virtual filesystem with paths like "/input_0.png" and
 * "/output.qgif". We map these to real filesystem operations.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef _WIN32
#include <fcntl.h>
#include <unistd.h>
#endif
#include <errno.h>
#include <stdint.h>

#include "qgif_generated.h"

/* Maximum number of open files */
#define MAX_FDS 64

/* File descriptor table */
static FILE* fd_table[MAX_FDS] = {NULL};

/* The w2c_a struct holds a pointer to the qgif instance for memory access */
struct w2c_a {
    w2c_qgif* qgif;
};

/* Helper to get next available FD (starting at 3 to avoid stdin/stdout/stderr) */
static int alloc_fd(void) {
    for (int i = 3; i < MAX_FDS; i++) {
        if (fd_table[i] == NULL) {
            return i;
        }
    }
    return -1;
}

/* Helper to read a C string from WASM memory */
static const char* read_string(struct w2c_a* ctx, u32 ptr) {
    wasm_rt_memory_t* mem = w2c_qgif_i(ctx->qgif);
    return (const char*)(mem->data + ptr);
}

/* Helper to get pointer to WASM memory */
static uint8_t* get_mem_ptr(struct w2c_a* ctx, u32 ptr) {
    wasm_rt_memory_t* mem = w2c_qgif_i(ctx->qgif);
    return mem->data + ptr;
}

/**
 * w2c_a_a - fd_close
 * Close a file descriptor
 */
u32 w2c_a_a(struct w2c_a* ctx, u32 fd) {
    if (fd >= MAX_FDS || fd < 3 || fd_table[fd] == NULL) {
        return 8; /* EBADF */
    }
    fclose(fd_table[fd]);
    fd_table[fd] = NULL;
    return 0;
}

/**
 * w2c_a_b - fd_write
 * Write to a file descriptor using iovec structure
 * fd: file descriptor
 * iov: pointer to iovec array in WASM memory
 * iovcnt: number of iovec entries
 * pnum: pointer to store number of bytes written
 */
u32 w2c_a_b(struct w2c_a* ctx, u32 fd, u32 iov, u32 iovcnt, u32 pnum) {
    if (fd >= MAX_FDS || fd_table[fd] == NULL) {
        /* Handle stdout/stderr */
        if (fd == 1 || fd == 2) {
            FILE* out = (fd == 1) ? stdout : stderr;
            uint8_t* mem = get_mem_ptr(ctx, 0);
            size_t total = 0;
            for (u32 i = 0; i < iovcnt; i++) {
                u32 ptr = *(u32*)(mem + iov + i * 8);
                u32 len = *(u32*)(mem + iov + i * 8 + 4);
                fwrite(mem + ptr, 1, len, out);
                total += len;
            }
            *(u32*)(mem + pnum) = total;
            return 0;
        }
        return 8; /* EBADF */
    }
    
    uint8_t* mem = get_mem_ptr(ctx, 0);
    size_t total = 0;
    
    for (u32 i = 0; i < iovcnt; i++) {
        u32 ptr = *(u32*)(mem + iov + i * 8);
        u32 len = *(u32*)(mem + iov + i * 8 + 4);
        size_t written = fwrite(mem + ptr, 1, len, fd_table[fd]);
        total += written;
        if (written < len) break;
    }
    
    *(u32*)(mem + pnum) = total;
    return 0;
}

/**
 * w2c_a_c - syscall_fcntl64
 * File control operations (mostly stubbed)
 */
u32 w2c_a_c(struct w2c_a* ctx, u32 fd, u32 cmd, u32 arg) {
    /* Most fcntl operations are not needed for basic file I/O */
    switch (cmd) {
        case 0: /* F_DUPFD */
        case 1: /* F_GETFD */
        case 2: /* F_SETFD */
        case 3: /* F_GETFL */
            return 0;
        case 4: /* F_SETFL */
            return 0;
        default:
            return -28; /* EINVAL */
    }
}

/**
 * w2c_a_d - emscripten_resize_heap
 * Request to grow memory (we pre-allocate enough, so this can return false)
 */
u32 w2c_a_d(struct w2c_a* ctx, u32 requested_size) {
    /* Memory growth is handled by wasm-rt, return success if within bounds */
    wasm_rt_memory_t* mem = w2c_qgif_i(ctx->qgif);
    if (requested_size <= mem->size) {
        return 1; /* Success */
    }
    /* Try to grow */
    if (wasm_rt_grow_memory(mem, (requested_size - mem->size + 65535) / 65536)) {
        return 1;
    }
    return 0; /* Failed */
}

/**
 * w2c_a_e - fd_seek
 * Seek in a file
 * fd: file descriptor
 * offset: seek offset (64-bit)
 * whence: SEEK_SET=0, SEEK_CUR=1, SEEK_END=2
 * new_offset: pointer to store new position
 */
u32 w2c_a_e(struct w2c_a* ctx, u32 fd, u64 offset, u32 whence, u32 new_offset) {
    if (fd >= MAX_FDS || fd < 3 || fd_table[fd] == NULL) {
        return 8; /* EBADF */
    }
    
    int origin;
    switch (whence) {
        case 0: origin = SEEK_SET; break;
        case 1: origin = SEEK_CUR; break;
        case 2: origin = SEEK_END; break;
        default: return 28; /* EINVAL */
    }
    
    if (fseek(fd_table[fd], (long)offset, origin) != 0) {
        return errno;
    }
    
    long pos = ftell(fd_table[fd]);
    uint8_t* mem = get_mem_ptr(ctx, 0);
    *(int64_t*)(mem + new_offset) = pos;
    
    return 0;
}

/**
 * w2c_a_f - fd_read
 * Read from a file descriptor using iovec structure
 */
u32 w2c_a_f(struct w2c_a* ctx, u32 fd, u32 iov, u32 iovcnt, u32 pnum) {
    if (fd >= MAX_FDS || fd_table[fd] == NULL) {
        return 8; /* EBADF */
    }
    
    uint8_t* mem = get_mem_ptr(ctx, 0);
    size_t total = 0;
    
    for (u32 i = 0; i < iovcnt; i++) {
        u32 ptr = *(u32*)(mem + iov + i * 8);
        u32 len = *(u32*)(mem + iov + i * 8 + 4);
        size_t read_bytes = fread(mem + ptr, 1, len, fd_table[fd]);
        total += read_bytes;
        if (read_bytes < len) break;
    }
    
    *(u32*)(mem + pnum) = total;
    return 0;
}

/**
 * w2c_a_g - syscall_ioctl
 * I/O control (mostly stubbed)
 */
u32 w2c_a_g(struct w2c_a* ctx, u32 fd, u32 op, u32 arg) {
    /* TTY operations for stdout/stderr */
    if (fd == 1 || fd == 2) {
        switch (op) {
            case 21509: /* TIOCGPGRP */
            case 21505: /* TCGETS */
                return -59; /* ENOTTY */
            default:
                return -28; /* EINVAL */
        }
    }
    return -28; /* EINVAL */
}

/**
 * w2c_a_h - syscall_openat
 * Open a file
 * dirfd: directory fd (ignored, we use absolute paths)
 * path: path string pointer in WASM memory
 * flags: O_RDONLY=0, O_WRONLY=1, O_RDWR=2, O_CREAT=64, O_TRUNC=512
 * mode: file mode (ignored)
 */
u32 w2c_a_h(struct w2c_a* ctx, u32 dirfd, u32 path_ptr, u32 flags, u32 mode) {
    const char* path = read_string(ctx, path_ptr);
    
    int fd = alloc_fd();
    if (fd < 0) {
        return -33; /* ENFILE - too many open files */
    }
    
    /* Determine fopen mode from flags */
    const char* fmode;
    int oflags = flags & 3; /* O_RDONLY=0, O_WRONLY=1, O_RDWR=2 */
    int create = flags & 64;  /* O_CREAT */
    int trunc = flags & 512;  /* O_TRUNC */
    int append = flags & 1024; /* O_APPEND */
    
    if (oflags == 0) {
        fmode = "rb";
    } else if (oflags == 1) {
        if (append) {
            fmode = "ab";
        } else if (create || trunc) {
            fmode = "wb";
        } else {
            fmode = "r+b";
        }
    } else {
        if (create || trunc) {
            fmode = "w+b";
        } else if (append) {
            fmode = "a+b";
        } else {
            fmode = "r+b";
        }
    }
    
    FILE* f = fopen(path, fmode);
    if (f == NULL) {
        return -errno;
    }
    
    fd_table[fd] = f;
    return fd;
}

/* Initialize the syscall context */
struct w2c_a* wasm_syscalls_init(w2c_qgif* qgif) {
    struct w2c_a* ctx = (struct w2c_a*)malloc(sizeof(struct w2c_a));
    ctx->qgif = qgif;
    
    /* Clear FD table */
    memset(fd_table, 0, sizeof(fd_table));
    
    return ctx;
}

/* Cleanup syscall context */
void wasm_syscalls_free(struct w2c_a* ctx) {
    /* Close any open files */
    for (int i = 3; i < MAX_FDS; i++) {
        if (fd_table[i] != NULL) {
            fclose(fd_table[i]);
            fd_table[i] = NULL;
        }
    }
    free(ctx);
}
