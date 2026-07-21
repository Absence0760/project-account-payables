"""Provision a new tenant from the command line.

Thin wrapper around app.services.tenant_provisioning.provision_tenant so
the CLI and the self-service /api/signup/complete endpoint share the same
code path.
"""

import argparse
import asyncio

from app.database import control_engine
from app.services.tenant_provisioning import provision_tenant


async def main():
    parser = argparse.ArgumentParser(description="Provision a new tenant")
    parser.add_argument("--name", required=True, help="Organization name")
    parser.add_argument("--slug", required=True, help="URL slug (e.g., 'acme')")
    parser.add_argument(
        "--plan",
        default="free",
        help="Organization.plan display label (default: free). Cosmetic only — "
        "every new tenant is bound to the real 'free' billing Subscription "
        "regardless of this value; upgrade via POST /api/billing/change-plan.",
    )
    parser.add_argument("--admin-email", required=True, help="Admin user email")
    parser.add_argument("--admin-password", required=True, help="Admin user password")
    parser.add_argument(
        "--admin-name", default=None, help="Admin full name (defaults to '<company> Admin')"
    )
    parser.add_argument(
        "--force-password-change",
        action="store_true",
        help="Require the admin to change their password on first login",
    )
    args = parser.parse_args()

    admin_name = args.admin_name or f"{args.name} Admin"

    print(f"Provisioning tenant: {args.name} (slug={args.slug})")
    result = await provision_tenant(
        company_name=args.name,
        slug=args.slug,
        admin_email=args.admin_email,
        admin_name=admin_name,
        admin_password=args.admin_password,
        plan=args.plan,
        must_change_password=args.force_password_change,
    )

    print(f"\nTenant '{args.slug}' is ready!")
    print(f"  Database:    {result.db_name}")
    print(f"  Login at:    http://{args.slug}.localhost:7777")
    print(f"  Login email: {args.admin_email}")
    print("  Password:    (the one you supplied via --admin-password)")

    await control_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
