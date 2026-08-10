from __future__ import annotations

import copy
import random
from dataclasses import asdict, dataclass
from typing import Any

from simulator.macro.config import ScenarioConfig, ScenarioEvent


STORY_STEPS = 100
STORY_CHECKPOINTS = (
    ("normal", "Normal network", 0),
    ("pressure", "First scheduled pressure", 20),
    ("response", "Forecast and optimizer response", 40),
    ("surprise", "Surprise and adaptation", 60),
    ("outcome", "Forecast versus reality", 100),
)


@dataclass(frozen=True, slots=True)
class StoryEpisodeTemplate:
    label: str
    group_index: int
    affected_class: str
    start_step: int
    end_step: int
    magnitude: float
    scheduled: bool
    known_at_step: int | None
    constrained_upf: str | None = None
    capacity_factor: float | None = None


_PLAYLISTS: tuple[tuple[StoryEpisodeTemplate, ...], ...] = (
    (
        StoryEpisodeTemplate("Stadium live upload", 1, "Conversational video", 24, 35, 2.45, True, 0, "upf-a", .52),
        StoryEpisodeTemplate("Enterprise sync surprise", 3, "Enterprise data", 44, 57, 3.10, False, None),
        StoryEpisodeTemplate("Residential streaming", 0, "Consumer broadband", 64, 76, 2.15, True, 40, "upf-c", .62),
        StoryEpisodeTemplate("Metro gaming rush", 2, "Low-latency gaming", 84, 97, 2.35, True, 60, "upf-b", .58),
    ),
    (
        StoryEpisodeTemplate("Enterprise backup window", 3, "Enterprise data", 23, 35, 2.35, True, 0, "upf-b", .55),
        StoryEpisodeTemplate("Metro voice surprise", 2, "Low-latency voice", 45, 57, 3.35, False, None),
        StoryEpisodeTemplate("Stadium creator surge", 1, "Conversational video", 64, 76, 2.55, True, 40, "upf-a", .50),
        StoryEpisodeTemplate("Residential prime time", 0, "Consumer broadband", 84, 97, 2.05, True, 60, "upf-c", .64),
    ),
    (
        StoryEpisodeTemplate("Residential software release", 0, "Consumer broadband", 24, 36, 2.10, True, 0, "upf-a", .56),
        StoryEpisodeTemplate("Stadium clip surprise", 1, "Conversational video", 44, 57, 3.20, False, None),
        StoryEpisodeTemplate("Metro tournament", 2, "Low-latency gaming", 64, 77, 2.45, True, 40, "upf-b", .57),
        StoryEpisodeTemplate("Enterprise close", 3, "Enterprise data", 84, 97, 2.30, True, 60, "upf-c", .63),
    ),
)


def build_story_playlist(config: ScenarioConfig, seed: int) -> tuple[ScenarioConfig, list[dict[str, Any]]]:
    """Select one bounded, prevalidated story without generating arbitrary traffic."""

    playlist_index = random.Random(seed).randrange(len(_PLAYLISTS))
    templates = _PLAYLISTS[playlist_index]
    groups = tuple(config.groups)
    events: list[ScenarioEvent] = []
    episodes: list[dict[str, Any]] = []
    for index, template in enumerate(templates, 1):
        group = groups[template.group_index]
        group_id = group.key.selection_id
        episode_id = f"episode-{index}"
        events.append(ScenarioEvent(
            step=template.start_step,
            event_type="arrival_factor",
            group_id=group_id,
            arrival_factor=template.magnitude,
            known_at_step=template.known_at_step,
            forecast_hint_multiplier=template.magnitude if template.scheduled else None,
        ))
        events.append(ScenarioEvent(
            step=template.end_step,
            event_type="arrival_factor",
            group_id=group_id,
            arrival_factor=1.0,
            known_at_step=template.known_at_step if template.scheduled else None,
            forecast_hint_multiplier=1.0 if template.scheduled else None,
        ))
        if template.constrained_upf and template.capacity_factor is not None:
            # The scheduled maintenance envelope is part of the same episode,
            # and is reset before the next decision boundary.
            events.extend((
                ScenarioEvent(
                    step=template.start_step + 2,
                    event_type="capacity_factor",
                    upf_id=template.constrained_upf,
                    ul_factor=template.capacity_factor,
                    dl_factor=template.capacity_factor,
                    known_at_step=template.known_at_step,
                ),
                ScenarioEvent(
                    step=min(template.end_step + 2, STORY_STEPS - 1),
                    event_type="capacity_factor",
                    upf_id=template.constrained_upf,
                    ul_factor=1.0,
                    dl_factor=1.0,
                    known_at_step=template.known_at_step,
                ),
            ))
        episodes.append({
            "id": episode_id,
            "order": index,
            "audience_label": template.label,
            "affected_class": template.affected_class,
            "group_id": group_id,
            "group_label": f"{group.key.zone} / {group.key.dnn}",
            "start_step": template.start_step,
            "end_step": template.end_step,
            "magnitude": template.magnitude,
            "scheduled": template.scheduled,
            "surprise": not template.scheduled,
            "known_at_step": template.known_at_step,
            "target_window_start_step": index * 20,
            "target_window_end_step": (index + 1) * 20,
            "constrained_upf": template.constrained_upf,
        })

    story_config = ScenarioConfig(
        scenario_id=config.scenario_id,
        seed=seed,
        start_time=config.start_time,
        steps=STORY_STEPS,
        step_seconds=30,
        decision_interval_steps=20,
        selection_audit_stride=config.selection_audit_stride,
        primary_overload_metric=config.primary_overload_metric,
        groups=groups,
        upfs=config.upfs,
        events=tuple(sorted(events, key=lambda item: (item.step, item.event_type, item.group_id or item.upf_id or ""))),
    )
    return story_config, copy.deepcopy(episodes)


def serialized_story_events(config: ScenarioConfig) -> list[dict[str, Any]]:
    return [asdict(event) for event in config.events]
