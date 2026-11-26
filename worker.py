"""ATLAS background worker entry point.

The worker is deliberately read-only: it captures Upstox option-chain snapshots,
runs the existing research engine, and stores decisions in the shared SQLite DB.
It has no broker order methods or order-routing credentials.
"""
from nandi_oi.worker import main


if __name__ == "__main__":
    main()
