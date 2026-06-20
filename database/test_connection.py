"""
Run from the project root:
    python -m database.test_connection
"""
import sys
from database.client import get_client

EXPECTED_TABLES = ["properties", "owners", "transactions", "listings", "scores"]


def main() -> None:
    client = get_client()

    print("Connecting to Supabase...\n")

    failed = False
    for table in EXPECTED_TABLES:
        try:
            # A limit-0 query is enough to confirm the table exists and RLS allows access
            client.table(table).select("*").limit(0).execute()
            print(f"  [OK]  {table}")
        except Exception as exc:
            print(f"  [FAIL] {table} — {exc}")
            failed = True

    print()
    if failed:
        print("One or more tables could not be reached. Check your credentials and schema.")
        sys.exit(1)
    else:
        print("All 5 tables confirmed. Database connection is healthy.")


if __name__ == "__main__":
    main()
