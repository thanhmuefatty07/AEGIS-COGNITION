# aegis-browser

Browser automation core abstractions for AEGIS.

This crate defines deterministic command packets, stealth configuration, session
persistence metadata, and a driver trait. A production CDP backend can implement
the trait while unit tests use the bundled in-memory driver.
