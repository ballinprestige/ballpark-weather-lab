# Third-Party Notices

This file is an attribution and provenance index, not legal advice and not a substitute for the
linked terms. Third-party rights are not granted by [LICENSE.md](LICENSE.md).

## Data and service providers

### MLB digital properties

Schedule, game-feed, venue, player, and Statcast-derived information may originate from MLB
digital properties. MLB and club names and marks belong to their respective owners. This project
is independent and not endorsed by MLB or its clubs.

- [MLB Terms of Use](https://www.mlb.com/official-information/terms-of-use)
- [MLB Stats API endpoint](https://statsapi.mlb.com/)
- [Baseball Savant park factors](https://baseballsavant.mlb.com/leaderboard/statcast-park-factors)

Only minimal daily projections and compact derived artifacts are published. Raw response archives,
row-level Statcast observations, and row-level training inputs are withheld.

### Open-Meteo

Weather forecasts and historical weather lineage use Open-Meteo. Open-Meteo data is licensed
under CC BY 4.0 subject to the provider's attribution requirements and service terms.

- [Open-Meteo](https://open-meteo.com/)
- [Open-Meteo data license](https://open-meteo.com/en/license)
- [Open-Meteo terms](https://open-meteo.com/en/terms)
- [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/)

The public interface should keep Open-Meteo attribution visible wherever its weather is displayed.
Values are transformed into Fahrenheit, miles per hour, wind components, and an air-density index;
source and validity timestamps remain attached.

### Retrosheet

Historical game-outcome lineage includes Retrosheet material. Retrosheet retains copyright and
requests acknowledgment.

- [Retrosheet](https://www.retrosheet.org/)
- [Retrosheet data-use notice](https://www.retrosheet.org/game.htm)

Row-level Retrosheet-derived training data is not redistributed here.

## Software dependencies

Major runtime and interface dependencies include:

- [XGBoost](https://github.com/dmlc/xgboost) — Apache License 2.0.
- [Apache Arrow / PyArrow](https://github.com/apache/arrow) — Apache License 2.0.
- [NumPy](https://github.com/numpy/numpy) — BSD 3-Clause License.
- [pandas](https://github.com/pandas-dev/pandas) — BSD 3-Clause License.
- [Requests](https://github.com/psf/requests) — Apache License 2.0.
- [jsonschema](https://github.com/python-jsonschema/jsonschema) — MIT License.
- [Svelte](https://github.com/sveltejs/svelte) — MIT License.
- [Vite](https://github.com/vitejs/vite) — MIT License.
- [Playwright](https://github.com/microsoft/playwright) — Apache License 2.0.

The interface self-hosts three Latin-subset fonts through Fontsource packages. The font files
remain under the SIL Open Font License 1.1; copyright notices and the complete license text ship
with the public bundle in `web/public/font-licenses.txt`.

- [DM Serif Display](https://fontsource.org/fonts/dm-serif-display) — Copyright 2014–2017 Adobe
  Systems Incorporated and Copyright 2019 Google LLC; SIL Open Font License 1.1.
- [IBM Plex Sans](https://fontsource.org/fonts/ibm-plex-sans) — Copyright 2019 IBM Corp.; SIL Open
  Font License 1.1.
- [JetBrains Mono](https://fontsource.org/fonts/jetbrains-mono) — Copyright 2020 The JetBrains Mono
  Project Authors; SIL Open Font License 1.1.

This summary may not enumerate every transitive package. `requirements.lock` and
`web/package-lock.json` are the machine-readable dependency inventories. Each dependency remains
subject to the license distributed by its project. A dependency-license scan is a publication
gate.

## Trademarks and venue facts

Team names, venue names, league names, logos, and other marks belong to their respective owners.
No logos are required by the interface. Venue coordinates, orientations, dimensions, and roof
states are approximate factual inputs and are not survey, safety, or facility-operating data.
The display geometry is a project-compiled approximation; its source-by-source observation ledger
was not preserved. It should not be represented as an export from, or verified by, any named
stadium database, mapping provider, or league source.
