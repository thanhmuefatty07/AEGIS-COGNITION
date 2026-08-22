FROM rust:bookworm AS rust-builder

WORKDIR /src
COPY rust-toolchain.toml ./
RUN RUST_VERSION=$(sed -n 's/^channel = "\([^"]*\)"/\1/p' rust-toolchain.toml) \
    && test -n "$RUST_VERSION" \
    && rustup toolchain install "$RUST_VERSION"
COPY Cargo.toml Cargo.lock ./
COPY core/rust/Cargo.toml core/rust/Cargo.toml
COPY core/rust core/rust
RUN cargo build --locked --release --manifest-path core/rust/Cargo.toml --bin aegis-nerve-cli

FROM python:3.14-slim AS runtime

ENV AEGIS_TRUST_LEVEL=PROD \
    AEGIS_OPERATOR_ROOT=/var/lib/aegis \
    AEGIS_ARTIFACTS_DIR=/var/lib/aegis/artifacts \
    PYTHONUNBUFFERED=1

WORKDIR /opt/aegis
RUN groupadd --system aegis && useradd --system --gid aegis --home-dir /opt/aegis aegis

COPY pyproject.toml PROJECT_OVERVIEW_DETAILED.md .env.example ./
COPY core/python core/python
COPY scripts scripts
COPY --from=rust-builder /src/target/release/aegis-nerve-cli /usr/local/bin/aegis-nerve-cli

RUN mkdir -p /var/lib/aegis/artifacts /var/lib/aegis/replay /var/lib/aegis/shadow-seals \
    && chown -R aegis:aegis /opt/aegis /var/lib/aegis

USER aegis
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -m core.python.operator_api_healthcheck || exit 1

CMD ["python", "-m", "core.python.operator_api_server", "--root", "/var/lib/aegis", "--host", "0.0.0.0", "--port", "8765"]
