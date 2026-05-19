"""
One-time fix script: Tag an existing service principal as an Enterprise Application.

Use this for service principals created before the tags fix was applied.
They exist in the directory but don't show in the Enterprise Applications blade.

Usage:
    python fix_existing_sp.py <service_principal_id>

Example:
    python fix_existing_sp.py aa1cf38a-74de-4914-b4ac-cfe3aac7748f
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python fix_existing_sp.py <service_principal_id>")
        print()
        print("Example:")
        print("  python fix_existing_sp.py aa1cf38a-74de-4914-b4ac-cfe3aac7748f")
        sys.exit(1)

    sp_id = sys.argv[1].strip()
    print(f"\nFixing service principal: {sp_id}")
    print("Adding tag: WindowsAzureActiveDirectoryIntegratedApp\n")

    from shared.graph_client import GraphClient
    client = GraphClient()

    try:
        # First verify the SP exists
        sp = await client._get(
            f"servicePrincipals/{sp_id}",
            params={"$select": "id,appId,displayName,tags"},
        )
        print(f"Found SP: {sp.get('displayName')} (appId={sp.get('appId')})")
        print(f"Current tags: {sp.get('tags', [])}")

        # Apply the fix
        await client.tag_as_enterprise_app(sp_id)
        print("\n✓ Tag applied successfully.")
        print("The application should now appear in Enterprise Applications.")
        print("If it doesn't appear immediately, wait 1-2 minutes and refresh.")

    except Exception as exc:
        print(f"\n✗ Failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

