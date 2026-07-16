"""Fresh-process driver for the atomic API submission loss contract."""

from __future__ import annotations

import sys

from openmagic_api.renewals import StartRenewalRequest, submit_renewal


def main() -> None:
    request = StartRenewalRequest.model_validate_json(sys.argv[2])
    submit_renewal(database_url=sys.argv[1], request=request)


if __name__ == "__main__":
    main()
