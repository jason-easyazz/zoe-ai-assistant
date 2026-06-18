# Locally-built wheels (NOT committed)

`omnigent` depends on `cel-expr-python`, which ships **no linux/aarch64 wheel and
no sdist** (upstream's `build_wheel.sh` hardcodes amd64 bazelisk). The Dockerfile
`COPY wheels/ /opt/wheels/` + `UV_FIND_LINKS=/opt/wheels` so `uv` resolves it locally.

Place the locally-built `cel_expr_python-*-linux_aarch64.whl` here before `docker build`.
The wheel itself is **git-ignored** (it is a ~14 MB platform binary) — build it from
source on the aarch64 host. See the module README for the build steps.
