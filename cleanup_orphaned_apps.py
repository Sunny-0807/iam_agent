"""
One-time cleanup script — deletes orphaned app registrations and service
principals created by failed onboarding attempts.

Usage:
    python cleanup_orphaned_apps.py <search_name>

Example:
    python cleanup_orphaned_apps.py "IAM Showcase SP Test"

Lists matches first, asks for confirmation, then deletes both the app
registration AND the service principal for each match.
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))


async def main():
    if len(sys.argv) < 2:
        print("Usage: python cleanup_orphaned_apps.py <search_name>")
        sys.exit(1)

    query = " ".join(sys.argv[1:]).strip()
    print(f"\nSearching for apps matching: '{query}'\n")

    from shared.graph_client import GraphClient
    client = GraphClient()

    # Search app registrations
    apps = await client.search_applications(query)
    if not apps:
        print("No apps found.")
        return

    print(f"Found {len(apps)} app registration(s):\n")
    for i, app in enumerate(apps, 1):
        name    = app.get("displayName", "")
        app_id  = app.get("appId", "")
        obj_id  = app.get("id", "")
        print(f"  [{i}] {name}")
        print(f"      Object ID  : {obj_id}")
        print(f"      App ID     : {app_id}")
        print()

    confirm = input(
        f"Delete ALL {len(apps)} app registration(s) and their service principals? [yes/no]: "
    ).strip().lower()

    if confirm != "yes":
        print("Aborted.")
        return

    print()
    for app in apps:
        name   = app.get("displayName", "")
        app_id = app.get("appId", "")
        obj_id = app.get("id", "")
        print(f"Processing: {name} (appId={app_id})")

        # Step 1: Clear identifierUris to allow SP deletion of SAML apps
        try:
            await client._patch(f"applications/{obj_id}", {"identifierUris": []})
            print(f"  ✅ identifierUris cleared on app registration")
        except Exception as exc:
            print(f"  ℹ Could not clear identifierUris (may not be needed): {exc}")

        # Step 2: Delete service principal
        try:
            sp = await client.get_service_principal_by_app_id(app_id)
            if sp:
                sp_id = sp["id"]
                await client.delete_service_principal(sp_id)
                print(f"  ✅ Service principal deleted: {sp_id}")
            else:
                print(f"  ℹ No service principal found for appId={app_id}")
        except Exception as exc:
            print(f"  ⚠ Could not delete SP: {exc}")

        # Step 3: Delete app registration
        try:
            await client.delete_application(obj_id)
            print(f"  ✅ App registration deleted: {obj_id}")
        except Exception as exc:
            print(f"  ⚠ Could not delete app registration: {exc}")

        print()

    print("Cleanup complete.")


if __name__ == "__main__":
    asyncio.run(main())

