# Portability tiers

## Tier 1 target

- Linux x86_64
- Windows x86_64
- macOS arm64

The CPU-only path is mandatory on every tier. Accelerator backends are optional
capability adapters and must not prevent provider-backed or CPU-only workflows from
starting.

## Tier 2 target

- Linux arm64
- Windows arm64
- macOS x86_64 while user demand justifies the maintenance cost

The current resource probe reports conservative portable signals. It does not yet
promise exact physical-core, NUMA, battery, thermal, GPU, or OS quota detection on
every platform.
