// Micro-benchmark: cost of the Gemma 4 E4B MTP draft head (Q4_0 [256 x N] matvec, N = vocab rows)
// versus the rest of one draft step's weight matvecs, on the CUDA backend. Safe next to the live
// brain (needs < 100 MiB). Build against the *installed* llama.cpp ggml (read-only):
//
//   G=~/llama.cpp-b11194; B=$G/build-jetson/bin
//   g++ -O2 -std=c++17 -I$G/ggml/include scripts/perf/mtp_head_microbench.cpp -o /path/out \
//       -L$B -lggml -lggml-base -lggml-cuda -Wl,-rpath,$B
//   flock /tmp/zoe-voice-harness.lock /path/out
//
// Output: ms per call (back-to-back, one sync) for each configuration, 3 repeats.
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-cuda.h"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

struct bench_t {
    ggml_backend_t be;
    ggml_context * ctx;
    ggml_cgraph * gf;
    ggml_backend_buffer_t buf;
};

static double now_ms() {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

// one linear: Q4_0 W [n_in x n_out] applied to f32 x [n_in x 1]; optional scatter of the output to n_full rows
static double run(ggml_backend_t be, const std::vector<std::pair<int, int>> & linears, int scatter_to, int iters) {
    ggml_init_params ip = {ggml_tensor_overhead() * 512 + ggml_graph_overhead(), nullptr, true};
    ggml_context * ctx = ggml_init(ip);
    ggml_cgraph * gf = ggml_new_graph(ctx);
    std::vector<ggml_tensor *> ws, xs;
    ggml_tensor * idx = nullptr;
    for (auto & l : linears) {
        ggml_tensor * w = ggml_new_tensor_2d(ctx, GGML_TYPE_Q4_0, l.first, l.second);
        ggml_tensor * x = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, l.first, 1);
        ws.push_back(w);
        xs.push_back(x);
    }
    if (scatter_to > 0) {
        idx = ggml_new_tensor_1d(ctx, GGML_TYPE_I64, linears[0].second);
    }
    ggml_tensor * last = nullptr;
    for (size_t i = 0; i < linears.size(); i++) {
        ggml_tensor * y = ggml_mul_mat(ctx, ws[i], xs[i]);
        if (i == 0 && scatter_to > 0) {
            ggml_tensor * full = ggml_fill(ctx, ggml_new_tensor_3d(ctx, GGML_TYPE_F32, 1, scatter_to, 1), -INFINITY);
            y = ggml_set_rows(ctx, full, ggml_reshape_3d(ctx, y, 1, linears[0].second, 1), ggml_reshape_3d(ctx, idx, linears[0].second, 1, 1));
        }
        ggml_build_forward_expand(gf, y);
        last = y;
    }
    (void) last;
    ggml_backend_buffer_t buf = ggml_backend_alloc_ctx_tensors(ctx, be);
    for (auto * t : ws) {
        std::vector<uint8_t> d(ggml_nbytes(t));
        for (auto & b : d) b = (uint8_t) (rand() & 0x77);  // small nibbles, finite scales
        // scale halves (first 2 bytes of each 18-byte block) -> 0x2400 (~0.25) to keep values finite
        for (size_t o = 0; o + 18 <= d.size(); o += 18) { d[o] = 0x00; d[o + 1] = 0x24; }
        ggml_backend_tensor_set(t, d.data(), 0, d.size());
    }
    for (auto * t : xs) {
        std::vector<float> d(ggml_nelements(t), 0.5f);
        ggml_backend_tensor_set(t, d.data(), 0, ggml_nbytes(t));
    }
    if (idx) {
        std::vector<int64_t> d(idx->ne[0]);
        for (size_t i = 0; i < d.size(); i++) d[i] = (int64_t) (i * (scatter_to / (double) d.size()));
        ggml_backend_tensor_set(idx, d.data(), 0, ggml_nbytes(idx));
    }
    for (int i = 0; i < 30; i++) ggml_backend_graph_compute(be, gf);
    ggml_backend_synchronize(be);
    double t0 = now_ms();
    for (int i = 0; i < iters; i++) ggml_backend_graph_compute(be, gf);
    ggml_backend_synchronize(be);
    double ms = (now_ms() - t0) / iters;
    ggml_backend_buffer_free(buf);
    ggml_free(ctx);
    return ms;
}

// the draft's backend sampler: top_k(10) over an n-wide f32 logit row (llama_sampler_top_k_backend_apply)
static double run_topk(ggml_backend_t be, int n, int iters) {
    ggml_init_params ip = {ggml_tensor_overhead() * 16 + ggml_graph_overhead(), nullptr, true};
    ggml_context * ctx = ggml_init(ip);
    ggml_cgraph * gf = ggml_new_graph(ctx);
    ggml_tensor * lg = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, n);
    ggml_tensor * tk = ggml_top_k(ctx, lg, 10);
    ggml_tensor * rows = ggml_get_rows(ctx, ggml_reshape_2d(ctx, lg, 1, n), tk);
    ggml_build_forward_expand(gf, rows);
    ggml_backend_buffer_t buf = ggml_backend_alloc_ctx_tensors(ctx, be);
    std::vector<float> d(n);
    for (int i = 0; i < n; i++) d[i] = (float) ((i * 2654435761u) % 10007) / 10007.0f;
    ggml_backend_tensor_set(lg, d.data(), 0, ggml_nbytes(lg));
    for (int i = 0; i < 30; i++) ggml_backend_graph_compute(be, gf);
    ggml_backend_synchronize(be);
    double t0 = now_ms();
    for (int i = 0; i < iters; i++) ggml_backend_graph_compute(be, gf);
    ggml_backend_synchronize(be);
    double ms = (now_ms() - t0) / iters;
    ggml_backend_buffer_free(buf);
    ggml_free(ctx);
    return ms;
}

int main() {
    ggml_backend_t be = ggml_backend_cuda_init(0);
    if (!be) { fprintf(stderr, "no CUDA backend\n"); return 1; }
    const int E = 256, V = 262144;
    // rest of ONE draft step (E4B assistant): 4 blocks + pre/post projections (all Q4_0 matvecs)
    std::vector<std::pair<int, int>> rest = {{5120, 256}, {256, 2560}};
    const int qo[4][2] = {{1024, 1024}, {1024, 1024}, {1024, 1024}, {2048, 2048}};  // q out dims / attn_o in dims
    for (int b = 0; b < 4; b++) {
        rest.push_back({E, qo[b][0]});          // attn_q
        rest.push_back({qo[b][1], E});          // attn_output
        rest.push_back({E, 2048});              // ffn_gate
        rest.push_back({E, 2048});              // ffn_up
        rest.push_back({2048, E});              // ffn_down
    }
    for (int rep = 0; rep < 3; rep++) {
        double head_full = run(be, {{E, V}}, 0, 400);
        double head_16k  = run(be, {{E, 16384}}, V, 400);       // sliced head + fill(-inf) + set_rows to 262144 (the patch)
        double head_16k_ns = run(be, {{E, 16384}}, 0, 400);     // sliced head, no scatter (lower bound)
        double head_32k  = run(be, {{E, 32768}}, V, 400);
        double rst       = run(be, rest, 0, 400);
        printf("rep %d: head_full=%.3f ms  head_16k+scatter=%.3f  head_16k_noscatter=%.3f  head_32k+scatter=%.3f  rest_of_draft=%.3f (%zu matvecs)\n",
               rep, head_full, head_16k, head_16k_ns, head_32k, rst, rest.size());
    }
    for (int rep = 0; rep < 3; rep++) {
        printf("rep %d: topk10_over_262144=%.3f ms  topk10_over_16384=%.3f ms  topk10_over_32768=%.3f ms\n", rep,
               run_topk(be, 262144, 400), run_topk(be, 16384, 400), run_topk(be, 32768, 400));
    }
    ggml_backend_free(be);
    return 0;
}
