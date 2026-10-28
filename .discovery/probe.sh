#!/usr/bin/env bash
# Phase 0 discovery probe: confirm dataset ids/fields/shape against the live API.
# Rate limit is 4 req/min per API -> batch of 4, then wait 62s.
set -u
cd "$(dirname "$0")" || exit 1
mkdir -p probe
IDS="cpi_headline cpi_headline_inflation cpi_core cpi_state cpi_state_inflation cpi_3d cpi_4d cpi_annual cpi_lowincome cpi_strata mcoicop lookup_item hh_income hh_income_state hh_income_district hies_state hies_malaysia_percentile hh_expenditure_dun wages_state wages_real_state formal_wages_state formal_wages wages_sector lfs_month lfs_state_sex pricecatcher fuelprice population_state productivity_annual economic_indicators ppi"
i=0
for id in $IDS; do
  if [ $((i % 4)) -eq 0 ] && [ "$i" -ne 0 ]; then sleep 62; fi
  code=$(curl -sL -o "probe/$id.json" -w "%{http_code}" \
    "https://api.data.gov.my/opendosm?id=$id&limit=1&meta=true")
  size=$(wc -c < "probe/$id.json")
  echo "$(date +%H:%M:%S) $id code=$code size=$size"
  i=$((i + 1))
done
echo "DONE"
