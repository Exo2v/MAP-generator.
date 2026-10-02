// Ashenfall - WorldPainter setup script (JSR-223)
//
//   Heightmap : ASHFALL_HEIGHTMAP_16BIT.png  (2048 x 2048, 16-bit, h = (Y + 64) / 384)
//   Populate  : ASHFALL_POPULATE_MASK.png
//   Biomes    : ASHFALL_BIOME_MAP.png
//
// Run from this folder with:  wpscript ashenfall_worldpainter_setup.js
// (WorldPainter -> Tools -> Run script... does the same thing.)
//
// The world is built to the specification's canvas: Minecraft 1.21.1, Y -64 .. 320,
// sea level Y = 62.  Chunks are NOT pre-decorated; the Populate layer is what tells the
// game (and with it Still Life) to run its own placed features.

var WORLD_NAME = 'Ashenfall';
var MIN_Y = -64, MAX_Y = 320, SEA_LEVEL = 62;

// ---- 1. the heightfield ------------------------------------------------------------
// A 16-bit height map comes in as levels 0..65535; map them onto the extended world
// height range so the abyssal trench and the glacial spine land on their exact Y.
var heightMap = wp.getHeightMap().fromFile('ASHFALL_HEIGHTMAP_16BIT.png').go();
var world = wp.createWorld()
        .fromHeightMap(heightMap)
        .fromLevels(0, 65535)
        .toLevels(MIN_Y, MAX_Y)
        .go();

// ---- 2. terrain materials by elevation (spec section 6) -----------------------------
// Terrain type indices are WorldPainter's own; see
// https://www.worldpainter.net/trac/wiki/Scripting/TerrainTypeValues
wp.applyHeightMap(heightMap)
        .toWorld(world)
        .applyToTerrain()
        .fromLevels(0, 13631).toTerrain(36)    // abyssal trench  -> Sandstone/beach
        .fromLevels(13632, 21503).toTerrain(39) // ocean floor     -> Gravel
        .fromLevels(21504, 22251).toTerrain(36) // shoreline       -> Beaches
        .fromLevels(22252, 32511).toTerrain(0)  // lowland         -> Grass
        .fromLevels(32512, 40703).toTerrain(3)  // highland        -> Permadirt
        .fromLevels(40704, 47359).toTerrain(29) // alpine          -> Rock
        .fromLevels(47360, 65535).toTerrain(24) // snow line       -> Snow
        .go();

// ---- 3. the Still Life populate mask (spec section 5, method 1) ---------------------
// Black = leave the chunk undecorated, white = let the game's decorators run.
try {
    var mask = wp.getHeightMap().fromFile('ASHFALL_POPULATE_MASK.png').go();
    var populate = wp.getLayer().withName('Populate').go();
    wp.applyHeightMap(mask)
            .toWorld(world)
            .applyToLayer(populate)
            .fromLevel(0).toLevel(0)
            .fromLevels(1, 255).toLevel(1)
            .go();
    wp.console.print('Ashenfall: populate layer applied from ASHFALL_POPULATE_MASK.png');
} catch (err) {
    wp.console.print('Ashenfall: could not apply the populate layer automatically (' +
                     err + '). Open the Populate layer and paint from ASHFALL_POPULATE_MASK.png instead - ' +
                     'white cells are the ones Still Life may decorate.');
}

// ---- 4. frost above the treeline ----------------------------------------------------
try {
    var frost = wp.getLayer().withName('Frost').go();
    wp.applyHeightMap(heightMap)
            .toWorld(world)
            .applyToLayer(frost)
            .fromLevels(0, 47359).toLevel(0)
            .fromLevels(47360, 65535).toLevel(1)
            .go();
} catch (err) {
    wp.console.print('Ashenfall: frost layer skipped (' + err + ')');
}

// ---- 5. save ------------------------------------------------------------------------
wp.saveWorld(world).toFile(WORLD_NAME + '.world').go();
wp.console.print('Ashenfall: world written to ' + WORLD_NAME + '.world');
