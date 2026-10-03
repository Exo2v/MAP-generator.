# builds/ — the ready-to-use mod

The `.jar` in this folder is **Vantraya Builder**, built from the sources in this repository. This is the file to put in a modpack.

```
vantraya-builder-neoforge-1.21.1-<version>.jar
└─ mod name ─┘ └ loader ┘ └ Minecraft ┘ └ the mod's version (mod_version in gradle.properties)
```

## Install

1. A **NeoForge 1.21.1** instance (Java 21). It is compiled against NeoForge 21.1.176; any 21.1.x should do.
2. Copy the jar into the instance's `mods/` folder — on the client and on the server.
3. *Create New World → World → World Type → **Vantraya***. Dedicated server: `level-type=vantraya_builder:vantraya` in `server.properties`.
4. In the new world, `/vantraya verify` re-checks the specification's landmarks against what was actually generated.

Lithosphere, Still Life, Tectonic and Lithostitched are optional; none of them is needed. How Vantraya Builder works next to them is in [`docs/COMPATIBILITY.md`](../docs/COMPATIBILITY.md).

## Where this jar comes from

* It is built by GitHub Actions (`.github/workflows/build.yml`), which runs **all the tests first** — the specification tests and the ones that start the real game engine — and commits the jar here only if every one of them passed. The commit message of the jar says which source commit it was built from and gives its SHA-256.
* `./gradlew build` does the same on your own machine: it runs the tests, then puts the jar in `builds/` (and in `build/libs/`).
* The build is reproducible: the same sources give the same bytes, so a rebuild of unchanged sources changes nothing in this folder.
* Only **one** jar is kept here, always the newest. Two versions of one mod in `mods/` stop the game from starting.

## Not yet play-tested

The tests prove the landmarks, elevations, climate tiers and the surface table, but nobody has walked around a Vantraya world in the game yet — see "Not verified" in the [README](../README.md#status-and-known-limits). If something is wrong, `/vantraya verify` and the lines of `latest.log` that start with `Vantraya:` are what is needed to fix it.
