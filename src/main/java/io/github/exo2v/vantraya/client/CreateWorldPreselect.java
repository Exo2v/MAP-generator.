package io.github.exo2v.vantraya.client;

import java.util.Collections;
import java.util.Set;
import java.util.WeakHashMap;

import io.github.exo2v.vantraya.VantrayaBuilder;
import io.github.exo2v.vantraya.mc.VantrayaConfig;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.worldselection.CreateWorldScreen;
import net.minecraft.client.gui.screens.worldselection.WorldCreationUiState;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.level.levelgen.presets.WorldPreset;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ScreenEvent;

/**
 * The optional {@code preselectWorldType} setting: opens the Create New World screen with Vantraya already chosen.
 *
 * <p>Off by default, and it only ever acts once per screen, at the moment the screen opens: whatever the player
 * picks afterwards stays picked, including when they come back from the data packs screen. Client only.
 */
@EventBusSubscriber(modid = VantrayaBuilder.MOD_ID, value = Dist.CLIENT)
public final class CreateWorldPreselect {
    private CreateWorldPreselect() {
    }

    private static final ResourceKey<WorldPreset> VANTRAYA = ResourceKey.create(Registries.WORLD_PRESET, VantrayaBuilder.id("vantraya"));

    /** Screens that were already given the choice: the event also fires again when the window is resized. */
    private static final Set<Screen> HANDLED = Collections.newSetFromMap(new WeakHashMap<>());

    @SubscribeEvent
    public static void onScreenInit(ScreenEvent.Init.Post event) {
        if (!(event.getScreen() instanceof CreateWorldScreen screen) || !VantrayaConfig.preselectWorldType() || !HANDLED.add(screen)) {
            return;
        }
        try {
            WorldCreationUiState state = screen.getUiState();
            for (WorldCreationUiState.WorldTypeEntry entry : state.getNormalPresetList()) {
                Holder<WorldPreset> preset = entry.preset();
                if (preset != null && preset.is(VANTRAYA)) {
                    state.setWorldType(entry);
                    VantrayaBuilder.LOGGER.info("Vantraya: the Vantraya world type is pre-selected on the Create New World screen");
                    return;
                }
            }
            VantrayaBuilder.LOGGER.warn("Vantraya: preselectWorldType is on, but the Vantraya world type is not in the list of world types");
        } catch (RuntimeException e) {
            VantrayaBuilder.LOGGER.warn("Vantraya: could not pre-select the Vantraya world type", e);
        }
    }
}
