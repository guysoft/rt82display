/**
 * Simple test program for QGIF native encoder
 */

#include <stdio.h>
#include <string.h>
#include "qgif_generated.h"

/* External functions from wasm_syscalls.c */
extern struct w2c_a* wasm_syscalls_init(w2c_qgif* qgif);
extern void wasm_syscalls_free(struct w2c_a* ctx);

int main(int argc, char** argv) {
    if (argc < 4) {
        printf("Usage: %s <input_pattern> <output.qgif> <frame_count>\n", argv[0]);
        printf("Example: %s /tmp/input_X.png /tmp/output.qgif 3\n", argv[0]);
        return 1;
    }
    
    const char* input_pattern = argv[1];
    const char* output_path = argv[2];
    int frame_count = atoi(argv[3]);
    
    printf("Input pattern: %s\n", input_pattern);
    printf("Output: %s\n", output_path);
    printf("Frames: %d\n", frame_count);
    
    /* Initialize wasm-rt */
    wasm_rt_init();
    
    /* Create QGIF instance */
    w2c_qgif qgif;
    memset(&qgif, 0, sizeof(qgif));
    
    /* Create syscall context */
    struct w2c_a* ctx = wasm_syscalls_init(&qgif);
    
    /* Instantiate WASM module */
    printf("Instantiating WASM module...\n");
    wasm2c_qgif_instantiate(&qgif, ctx);
    
    /* Run initialization (export 'j') */
    printf("Running WASM init...\n");
    w2c_qgif_j(&qgif);
    
    /* Get memory info */
    wasm_rt_memory_t* mem = w2c_qgif_i(&qgif);
    printf("Memory: %llu pages, %llu bytes\n", mem->pages, mem->size);
    
    /* Allocate string for input pattern */
    size_t input_len = strlen(input_pattern) + 1;
    u32 input_ptr = w2c_qgif_l(&qgif, input_len);
    memcpy(mem->data + input_ptr, input_pattern, input_len);
    
    /* Allocate string for output path */
    size_t output_len = strlen(output_path) + 1;
    u32 output_ptr = w2c_qgif_l(&qgif, output_len);
    memcpy(mem->data + output_ptr, output_path, output_len);
    
    /* Call compress_video_wasm (export 'm') */
    printf("Calling compress_video_wasm...\n");
    u32 result = w2c_qgif_m(&qgif, input_ptr, output_ptr, 0, frame_count);
    
    if (result == 0) {
        printf("✅ Success!\n");
    } else {
        printf("❌ Failed with code: %u\n", result);
    }
    
    /* Cleanup */
    w2c_qgif_k(&qgif, input_ptr);
    w2c_qgif_k(&qgif, output_ptr);
    wasm2c_qgif_free(&qgif);
    wasm_syscalls_free(ctx);
    wasm_rt_free();
    
    return result;
}
