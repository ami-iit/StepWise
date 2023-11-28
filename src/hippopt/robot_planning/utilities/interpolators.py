import casadi as cs
import liecasadi
import numpy as np

from hippopt import StorageType
from hippopt.robot_planning.variables.contacts import (
    ContactPointDescriptor,
    FeetContactPhasesDescriptor,
    FeetContactPointDescriptors,
    FeetContactPoints,
    FootContactPhaseDescriptor,
    FootContactState,
)
from hippopt.robot_planning.variables.floating_base import (
    FloatingBaseSystemState,
    FreeFloatingObjectState,
    KinematicTreeState,
)
from hippopt.robot_planning.variables.humanoid import HumanoidState


def linear_interpolator(
    initial: StorageType, final: StorageType, number_of_points: int
) -> list[StorageType]:
    assert not isinstance(initial, list) and not isinstance(final, list)

    interpolator = cs.interpolant("lerp", "linear", [initial, final], [0.0, 1.0])
    x = np.linspace(start=0.0, stop=1.0, num=number_of_points)
    return [interpolator(x_i) for x_i in x]


def quaternion_slerp(
    initial: StorageType, final: StorageType, number_of_points: int
) -> list[StorageType]:
    assert not isinstance(initial, list) and not isinstance(final, list)

    x = np.linspace(start=0.0, stop=1.0, num=number_of_points)
    return [liecasadi.Quaternion.slerp_step(initial, final, t) for t in x]


def transform_interpolator(
    initial: liecasadi.SE3, final: liecasadi.SE3, number_of_points: int
) -> list[liecasadi.SE3]:
    linear_interpolation = linear_interpolator(
        initial=initial.translation(),
        final=final.translation(),
        number_of_points=number_of_points,
    )
    quaternion_interpolation = quaternion_slerp(
        initial=initial.rotation(),
        final=final.rotation(),
        number_of_points=number_of_points,
    )
    output = []
    for i in range(number_of_points):
        output.append(
            liecasadi.SE3(quaternion_interpolation[i], linear_interpolation[i])
        )
    return output


def foot_contact_state_interpolator(
    phases: list[FootContactPhaseDescriptor],
    descriptor: list[ContactPointDescriptor],
    number_of_points: int,
    dt: float,
    t0: float = 0.0,
) -> list[FootContactState]:
    assert len(phases) > 0
    assert number_of_points > 0
    assert dt > 0.0

    end_time = t0 + dt * number_of_points

    if phases[0].activation_time is None:
        deactivation_time = (
            phases[0].deactivation_time
            if phases[0].deactivation_time is not None
            else t0
        )
        phases[0].activation_time = min(deactivation_time, t0) - dt

    for i, phase in enumerate(phases):
        if phase.activation_time is None:
            raise ValueError(
                f"Phase {i} has no activation time, but is not the first phase."
            )

    last = len(phases) - 1
    if phases[last].deactivation_time is None:
        phases[last].deactivation_time = (
            max(end_time, phases[last].activation_time) + dt
        )

    if phases[last].deactivation_time < end_time:
        raise ValueError(
            f"The Last phase deactivation time "
            f"({phases[len(phases) - 1].deactivation_time}) is before "
            f"the end time ({end_time}, computed from the inputs)."
        )

    for i, phase in enumerate(phases):
        if phase.deactivation_time is None:
            raise ValueError(
                f"Phase {i} has no deactivation time, but is not the last phase."
            )
        if phase.activation_time > phase.deactivation_time:
            raise ValueError(
                f"Phase {i} has an activation time ({phase.activation_time}) "
                f"greater than its deactivation time ({phase.deactivation_time})."
            )

        if i < last:
            if phase.deactivation_time > phases[i + 1].activation_time:
                raise ValueError(
                    f"Phase {i} has a deactivation time ({phase.deactivation_time}) "
                    f"greater than the activation time of the next phase "
                    f"({phases[i + 1].activation_time})."
                )

    output = []

    def append_stance_phase(
        stance_phase: FootContactPhaseDescriptor,
        points: int,
    ) -> None:
        for _ in range(points):
            foot_state = FootContactState.from_parent_frame_transform(
                descriptor=descriptor, transform=stance_phase.transform
            )
            for point in foot_state:
                point.f = stance_phase.force
            output.append(foot_state)

    def append_swing_phase(
        start_phase: FootContactPhaseDescriptor,
        end_phase: FootContactPhaseDescriptor,
        points: int,
    ):
        full_swing_points = int(
            np.ceil((end_phase.activation_time - start_phase.deactivation_time) / dt)
        )
        mid_swing_points = min(round(full_swing_points / 2), points)
        mid_swing_transforms = transform_interpolator(
            start_phase.transform, start_phase.mid_swing_transform, mid_swing_points
        )
        for transform in mid_swing_transforms:
            foot_state = FootContactState.from_parent_frame_transform(
                descriptor=descriptor, transform=transform
            )
            for point in foot_state:
                point.f = 0.0
            output.append(foot_state)
        second_half_points = points - mid_swing_points
        if second_half_points == 0:
            return
        second_half_transforms = transform_interpolator(
            start_phase.mid_swing_transform, end_phase.transform, second_half_points
        )
        for transform in second_half_transforms:
            foot_state = FootContactState.from_parent_frame_transform(
                descriptor=descriptor, transform=transform
            )
            for point in foot_state:
                point.f = end_phase.force
            output.append(foot_state)

    if len(phases) == 1 or phases[0].deactivation_time >= end_time:
        append_stance_phase(phases[0], number_of_points)
        return output

    remaining_points = number_of_points
    for i in range(len(phases) - 1):
        phase = phases[i]
        next_phase = phases[i + 1]

        stance_points = int(
            np.ceil((phase.deactivation_time - phase.activation_time) / dt)
        )
        stance_points = min(stance_points, remaining_points)

        append_stance_phase(phase, stance_points)
        remaining_points -= stance_points

        if remaining_points == 0:
            return output

        swing_points = int(
            np.ceil((next_phase.activation_time - phase.deactivation_time) / dt)
        )

        swing_points = min(swing_points, remaining_points)

        if swing_points == 0:
            continue

        append_swing_phase(phase, next_phase, swing_points)
        remaining_points -= swing_points

        if remaining_points == 0:
            return output

    last_phase = phases[len(phases) - 1]
    append_stance_phase(last_phase, remaining_points)
    return output


def feet_contact_points_interpolator(
    phases: FeetContactPhasesDescriptor,
    descriptor: FeetContactPointDescriptors,
    number_of_points: int,
    dt: float,
    t0: float = 0.0,
) -> list[FeetContactPoints]:
    left_output = foot_contact_state_interpolator(
        phases=phases.left,
        descriptor=descriptor.left,
        number_of_points=number_of_points,
        dt=dt,
        t0=t0,
    )
    right_output = foot_contact_state_interpolator(
        phases=phases.right,
        descriptor=descriptor.right,
        number_of_points=number_of_points,
        dt=dt,
        t0=t0,
    )

    assert len(left_output) == len(right_output) == number_of_points

    output = []
    for i in range(number_of_points):
        output_state = FeetContactPoints()
        output_state.left = left_output[i]
        output_state.right = right_output[i]
        output.append(output_state)

    return output


def free_floating_object_state_interpolator(
    initial_state: FreeFloatingObjectState,
    final_state: FreeFloatingObjectState,
    number_of_points: int,
) -> list[FreeFloatingObjectState]:
    position_interpolation = linear_interpolator(
        initial=initial_state.position,
        final=final_state.position,
        number_of_points=number_of_points,
    )
    quaternion_interpolation = quaternion_slerp(
        initial=initial_state.quaternion_xyzw,
        final=final_state.quaternion_xyzw,
        number_of_points=number_of_points,
    )
    assert (
        len(position_interpolation) == len(quaternion_interpolation) == number_of_points
    )
    output = []
    for i in range(number_of_points):
        output_state = FreeFloatingObjectState()
        output_state.position = position_interpolation[i]
        output_state.quaternion_xyzw = quaternion_interpolation[i]
        output.append(output_state)
    return output


def kinematic_tree_state_interpolator(
    initial_state: KinematicTreeState,
    final_state: KinematicTreeState,
    number_of_points: int,
) -> list[KinematicTreeState]:
    if len(initial_state.positions) != len(final_state.positions):
        raise ValueError(
            f"Initial state has {len(initial_state.positions)} joints, "
            f"but final state has {len(final_state.positions)} joints."
        )

    positions_interpolation = linear_interpolator(
        initial=initial_state.positions,
        final=final_state.positions,
        number_of_points=number_of_points,
    )
    output = []
    for i in range(number_of_points):
        output_state = KinematicTreeState(
            number_of_joints_state=len(initial_state.positions)
        )
        output_state.positions = positions_interpolation[i]
        output.append(output_state)
    return output


def floating_base_system_state_interpolator(
    initial_state: FloatingBaseSystemState,
    final_state: FloatingBaseSystemState,
    number_of_points: int,
) -> list[FloatingBaseSystemState]:
    base_interpolation = free_floating_object_state_interpolator(
        initial_state=initial_state.base,
        final_state=final_state.base,
        number_of_points=number_of_points,
    )
    joints_interpolation = kinematic_tree_state_interpolator(
        initial_state=initial_state.joints,
        final_state=final_state.joints,
        number_of_points=number_of_points,
    )

    assert len(base_interpolation) == len(joints_interpolation) == number_of_points

    output = []
    for i in range(number_of_points):
        output_state = FloatingBaseSystemState(
            number_of_joints_state=len(initial_state.joints.positions)
        )
        output_state.base = base_interpolation[i]
        output_state.joints = joints_interpolation[i]
        output.append(output_state)
    return output


def humanoid_state_interpolator(
    initial_state: HumanoidState,
    final_state: HumanoidState,
    contact_phases: FeetContactPhasesDescriptor,
    contact_descriptor: FeetContactPointDescriptors,
    number_of_points: int,
    dt: float,
    t0: float = 0.0,
):
    contact_points_interpolation = feet_contact_points_interpolator(
        phases=contact_phases,
        descriptor=contact_descriptor,
        number_of_points=number_of_points,
        dt=dt,
        t0=t0,
    )
    kinematics_interpolation = floating_base_system_state_interpolator(
        initial_state=initial_state.kinematics,
        final_state=final_state.kinematics,
        number_of_points=number_of_points,
    )
    com_interpolation = linear_interpolator(
        initial=initial_state.com,
        final=final_state.com,
        number_of_points=number_of_points,
    )

    assert (
        len(contact_points_interpolation)
        == len(kinematics_interpolation)
        == len(com_interpolation)
        == number_of_points
    )

    output = []
    for i in range(number_of_points):
        output_state = HumanoidState(
            contact_point_descriptors=contact_descriptor,
            number_of_joints=len(initial_state.kinematics.joints.positions),
        )
        output_state.contact_points = contact_points_interpolation[i]
        output_state.kinematics = kinematics_interpolation[i]
        output_state.com = com_interpolation[i]
        output.append(output_state)
    return output