# Landscape: comparable sites and alternative data sources

Reference only. Nothing here is a data source for this project unless it also appears
in `CLAUDE.md`'s data flow. Recorded so we know who else occupies this space and where
a second AIS feed could come from.

## Comparable sites

### darkships.org

"Tracking the shadow fleet from free AIS & satellite data." The closest thing to a
direct comparable: same free-AIS ingredients, same dark-fleet / sanctions-adjacent
framing, and it surfaced in the aisstream issue tracker as a reference point during
the 2026-08-05 outage ("down again, you can see it here too") - which is itself the
useful fact about it.

**Not a data source, and not scrapable.**

- It is *downstream of the same feeds we use*, not an independent observer. It went
  dark in the same outage, at the same time. Reading it during an outage returns the
  same nothing, with someone else's uptime added to our dependency chain.
- `robots.txt` sets `Content-Signal: search=yes,ai-train=no,use=reference` and
  explicitly `Disallow: /` for `ClaudeBot`, `GPTBot`, `CCBot`, `Google-Extended`,
  `Bytespider`, `Amazonbot`, `Applebot-Extended`, `meta-externalagent` and
  `CloudflareBrowserRenderingCrawler`.
- It is a Cloudflare-fronted SPA that returns only a `<title>` to a plain fetch, so
  harvesting it would require the headless renderer its robots.txt specifically
  refuses. Checked 2026-08-07.

Worth watching for what they build, not for what we can take.

**Their Sources page is the genuinely valuable part** (read 2026-08-07), and not
because it is theirs to give: the 18 lists it names are public primary sources with
permissive licences, so anything we wanted from them we would ingest from the
originals. OFAC SDN (public domain), Ukraine GUR, EU Annex XLII (public sector),
Canada SEMA (official gov XML), UK FCDO (Open Government Licence), Australia DFAT and
NZ MFAT (CC BY 4.0), Switzerland SECO, Paris MoU port bans, UN Security Council 1718,
the RFMO IUU fishing blacklists (EU/ICCAT/WCPFC), and Tokyo / Black Sea / Abuja MoU
detention lists. Treat the page as a bibliography.

Their headline figures, for calibration: 2,658 deduplicated sanctioned-or-banned
vessels against a ~1,400 RUSI/Windward estimate of the Russian shadow fleet (theirs is
larger because it also spans Iran, Venezuela and North Korea), and 8,942 once
port-state detentions are added.

The methodological point worth stealing is the one they are careful about: a sanctions
or port-ban designation and a port-state-control detention are different kinds of
claim, and they count them separately - detentions are "corroboration only, not
designations." Any version of this we built would have to hold that line.

**But note the standing decision against building this at all.** The root `CLAUDE.md`
lists "External sanctions lists ingestion" under Deliberately Not Building: risk
scoring here uses only owned data and public registry facts, because matching named
sanctioned entities is a legal-grade exercise this project should not pretend to do.
Nothing above overturns that - the data being free and well-licensed was never the
obstacle. The obstacle is that a false positive against a named vessel is a
consequential claim. Reopen deliberately or not at all.

## Alternative AIS sources (redundancy)

The 2026-08-05 aisstream outage put the live tracker at zero for over a day with no
fallback. Single-source risk is now a known, demonstrated weakness rather than a
theoretical one. See ROADMAP backlog for the actual work item.

### The requirement that rules out most candidates

**A fallback must carry vessel type and dimensions, not just positions.** Our
`kind`/`segment` labels come from `ais.classify(ship_type, length_m)`, sourced from AIS
`ShipStaticData`. A positions-only feed yields uncoloured dots and silently breaks
everything keyed on segment: the tracker legend, the cycle board, the analytics tiles,
and supply-nowcast's storage curve. Check this **first** on any provider, because it is
the cheapest question to answer and it eliminates most of them.

### AISHub - ruled out (verified 2026-08-09)

Checked against AISHub's own join page rather than community hearsay, and it closes
every door:

- *"AISHub is a contributor-based network. Applications without an operational AIS
  station and feed will not be approved."* No consumer tier, at any price.
- Third-party data is banned by name: no scraped data, and specifically no feeds
  *"from publicly available AIS sources or services"* - so relaying our aisstream feed
  back to qualify is prohibited by them, and would breach aisstream's terms too.
- You cannot use someone else's station.
- API access additionally requires, as 7-day averages: **>=10 vessels, >=90% uptime,
  <10s message delay, <=60s sampling.**

Independently, the operator is in **Geneva** - landlocked. A receiver here would see
Lake Geneva craft: almost certainly short of the 10-vessel bar, and the wrong data
regardless, since none of it is tankers or bulkers. The only legitimate route is a
receiver hosted by someone within VHF range of real traffic (~EUR 60 of RTL-SDR plus a
standing favour), or one of the free-receiver programmes (MarineTraffic ships hardware
to volunteers) which also require a coastal location. Not pursued.

Worth knowing: another project hit this identically and wrote it up
(`koala73/worldmonitor#6227`, "AIS has no fallback"), ruling AISHub out on the same
sentence and pivoting to provider selection.

### Data Docked - ruled out on pricing model, not on price (verified 2026-08-09)

Evaluated because it advertises a free tier and full vessel particulars. Both true, and
neither helps:

- **Type is not inline with positions.** The area/bounding-box query returns position
  fields; `shipType`, length and beam come from a separate *Vessel Particulars*
  endpoint, one call per vessel. That fails the requirement above without a per-vessel
  enrichment pass.
- **It is credit-metered REST, not a stream.** Area query costs 10 credits per call
  regardless of vessels returned; particulars cost 1 credit each. The free tier is
  20 credits (100 on some listings); paid starts around EUR 80/mo.
- **The arithmetic is not close.** Our collector watches 29 basins. Polling each even
  once per 10 minutes is 29 x 10 x 144 = ~41,760 credits/day, before a single
  particulars call, and enriching the 32k vessels we have seen is another 32,000. A
  websocket firehose is simply a different product from a per-call API, and no plan
  tier closes a gap of that shape.

It could serve a *narrow* fallback - the 13 chokepoints only, at low frequency - which
is the shape worldmonitor landed on with VesselFinder LiveData's fixed-area pricing. If
redundancy is ever funded, scope it that way rather than as a like-for-like replacement.

### Still unverified

National open-data AIS feeds are genuinely independent, free, and need no hardware or
contribution, but each covers only its own waters. Norway (Kystverket/BarentsWatch),
Denmark (Danish Maritime Authority) and Finland (Fintraffic/Digitraffic) all publish
open AIS. For us they would cover `skaw_danish_straits` and `primorsk_baltic`: about 3%
of collected rows. A supplement, and an honest one, but not a replacement.

## Deliberately not pursued

- **Scraping any comparable site.** Covered above for darkships.org, but the principle
  is general: a scraped mirror is not redundancy, and taking content from a site that
  refuses automated access is not something this project does.
- **Paid/satellite AIS.** Unchanged from the root roadmap: against the free-source
  ethos, and coverage gaps are disclosed rather than bought around.
