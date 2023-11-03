import copy
import dataclasses
import logging
import os
import pathlib
import time

import ffmpeg
import liecasadi
import numpy as np
from idyntree.visualize import MeshcatVisualizer

import hippopt.deps.surf2stl as surf2stl
from hippopt.robot_planning.utilities.terrain_descriptor import TerrainDescriptor
from hippopt.robot_planning.variables.humanoid import (
    FeetContactPointDescriptors,
    HumanoidState,
)


@dataclasses.dataclass
class HumanoidStateVisualizerSettings:
    robot_model: str = dataclasses.field(default=None)
    considered_joints: list[str] = dataclasses.field(default=None)
    contact_points: FeetContactPointDescriptors = dataclasses.field(default=None)
    robot_color: list[float] = dataclasses.field(default=None)
    ground_color: list[float] = dataclasses.field(default=None)
    terrain: TerrainDescriptor = dataclasses.field(default=None)
    com_color: list[float] = dataclasses.field(default=None)
    com_radius: float = dataclasses.field(default=None)
    contact_points_color: list[float] = dataclasses.field(default=None)
    contact_points_radius: float = dataclasses.field(default=None)
    contact_forces_color: list[float] = dataclasses.field(default=None)
    contact_force_radius: float = dataclasses.field(default=None)
    contact_force_scaling: float = dataclasses.field(default=None)
    working_folder: str = dataclasses.field(default=None)
    ground_mesh_axis_points: int = dataclasses.field(default=None)
    ground_x_limits: list[float] = dataclasses.field(default=None)
    ground_y_limits: list[float] = dataclasses.field(default=None)

    def __post_init__(self):
        self.robot_color = [1, 1, 1, 0.25]
        self.ground_color = [0.5, 0.5, 0.5, 0.75]
        self.com_color = [1, 0, 0, 1]
        self.com_radius = 0.02
        self.contact_points_color = [0, 0, 0, 1]
        self.contact_forces_color = [1, 0, 0, 1]
        self.contact_points_radius = 0.01
        self.contact_force_radius = 0.005
        self.contact_force_scaling = 0.01
        self.ground_x_limits = [-1.5, 1.5]
        self.ground_y_limits = [-1.5, 1.5]
        self.ground_mesh_axis_points = 200
        self.working_folder = "./"

    def is_valid(self):
        ok = True
        logger = logging.getLogger("[hippopt::HumanoidStateVisualizerSettings]")
        if self.robot_model is None:
            logger.error("robot_model is not specified.")
            ok = False
        if self.considered_joints is None:
            logger.error("considered_joints is not specified.")
            ok = False
        if self.contact_points is None:
            logger.error("contact_points is not specified.")
            ok = False
        if not os.access(self.working_folder, os.W_OK):
            logger.error("working_folder is not writable.")
            ok = False
        if len(self.robot_color) != 4:
            logger.error("robot_color is not specified correctly.")
            ok = False
        if len(self.ground_color) != 4:
            logger.error("ground_color is not specified correctly.")
            ok = False
        if len(self.com_color) != 4:
            logger.error("com_color is not specified correctly.")
            ok = False
        if len(self.contact_points_color) != 4:
            logger.error("contact_points_color is not specified correctly.")
            ok = False
        if len(self.contact_forces_color) != 4:
            logger.error("contact_forces_color is not specified correctly.")
            ok = False
        if self.com_radius <= 0:
            logger.error("com_radius is not specified correctly.")
            ok = False
        if self.contact_points_radius <= 0:
            logger.error("contact_points_radius is not specified correctly.")
            ok = False
        if self.contact_force_radius <= 0:
            logger.error("contact_force_radius is not specified correctly.")
            ok = False
        if self.ground_mesh_axis_points <= 0:
            logger.error("ground_mesh_axis_points is not specified correctly.")
            ok = False
        if self.terrain is None:
            logger.error("terrain is not specified.")
            ok = False
        if len(self.ground_x_limits) != 2 or (
            self.ground_x_limits[0] >= self.ground_x_limits[1]
        ):
            logger.error("ground_x_limits are not specified correctly.")
            ok = False
        if len(self.ground_y_limits) != 2 or (
            self.ground_y_limits[0] >= self.ground_y_limits[1]
        ):
            logger.error("ground_y_limits are not specified correctly.")
            ok = False
        return ok


class HumanoidStateVisualizer:
    def __init__(self, settings: HumanoidStateVisualizerSettings) -> None:
        if not settings.is_valid():
            raise ValueError("Settings are not valid.")
        self._logger = logging.getLogger("[hippopt::HumanoidStateVisualizer]")
        self._settings = settings
        self._create_ground_urdf()
        self._create_ground_mesh()
        self._viz = MeshcatVisualizer()
        self._viz.load_model_from_file(
            model_path=settings.robot_model,
            model_name="robot",
            considered_joints=settings.considered_joints,
            color=settings.robot_color,
        )
        self._viz.load_model_from_file(
            model_path=os.path.join(self._settings.working_folder, "ground.urdf"),
            model_name="ground",
            color=settings.ground_color,
        )
        self._viz.load_sphere(
            radius=settings.com_radius, shape_name="CoM", color=settings.com_color
        )
        for i, point in enumerate(
            (settings.contact_points.left + settings.contact_points.right)
        ):
            self._viz.load_sphere(
                radius=settings.contact_points_radius,
                shape_name=f"p_{i}",
                color=settings.contact_points_color,
            )
            self._viz.load_cylinder(
                radius=settings.contact_force_radius,
                length=1.0,
                shape_name=f"f_{i}",
                color=settings.contact_forces_color,
            )

    def _create_ground_urdf(self):
        with open(os.path.join(self._settings.working_folder, "ground.urdf"), "w") as f:
            f.write(
                """<?xml version="1.0"?>
                <robot name="ground">
                    <link name="world"/>
                    <link name="link_0">
                        <inertial>
                            <origin xyz="0.0 0.0 0.0" rpy="0.0 0.0 0.0"/>
                            <mass value="1.0"/>
                            <inertia ixx="0.0" ixy="0.0" ixz="0.0"
                                     iyy="0.0" iyz="0.0" izz="0.0"/>
                        </inertial>
                        <visual>
                            <origin xyz="0.0 0.0 0.0" rpy="0.0 0.0 0.0"/>
                            <geometry>
                                <mesh filename="./ground.stl" scale="1.0 1.0 1.0"/>
                            </geometry>
                            <material name="ground_color">
                                 <color rgba="0.5 0.5 0.5 1"/>
                            </material>
                        </visual>
                        <collision>
                            <origin xyz="0.0 0.0 0.0" rpy="0.0 0.0 0.0"/>
                            <geometry>
                                 <mesh filename="./ground.stl" scale="1.0 1.0 1.0"/>
                            </geometry>
                        </collision>
                    </link>
                    <joint name="joint_world_link_0" type="fixed">
                        <origin xyz="0.0 0.0 0.0" rpy="0.0 0.0 0.0"/>
                        <parent link="world"/>
                        <child link="link_0"/>
                    </joint>
                </robot>
                """
            )

    def _create_ground_mesh(self):
        x_step = (
            self._settings.ground_x_limits[1] - self._settings.ground_x_limits[0]
        ) / self._settings.ground_mesh_axis_points
        y_step = (
            self._settings.ground_y_limits[1] - self._settings.ground_y_limits[0]
        ) / self._settings.ground_mesh_axis_points
        x = np.arange(
            self._settings.ground_x_limits[0],
            self._settings.ground_x_limits[1] + x_step,
            x_step,
        )
        y = np.arange(
            self._settings.ground_y_limits[0],
            self._settings.ground_y_limits[1] + y_step,
            y_step,
        )
        x, y = np.meshgrid(x, y)
        assert x.shape == y.shape
        points = np.array([x.flatten(), y.flatten(), np.zeros(x.size)])
        height_function_map = self._settings.terrain.height_function().map(x.size)
        z = -np.array(height_function_map(points).full()).reshape(x.shape)
        surf2stl.write(
            os.path.join(self._settings.working_folder, "ground.stl"), x, y, z
        )

    @staticmethod
    def _skew(x: np.ndarray) -> np.ndarray:
        return np.array(
            [
                [0, -x[2], x[1]],
                [x[2], 0, -x[0]],
                [-x[1], x[0], 0],
            ]
        )

    def _visualize_single_state(
        self, state: HumanoidState, save: bool, file_name_stem: str
    ):
        self._viz.set_multibody_system_state(
            state.kinematics.base.position,
            liecasadi.SO3.from_quat(state.kinematics.base.quaternion_xyzw)
            .as_matrix()
            .full(),
            state.kinematics.joints.positions,
            "robot",
        )
        self._viz.set_primitive_geometry_transform(
            state.com,
            np.eye(3),
            "CoM",
        )

        for i, point in enumerate(
            (state.contact_points.left + state.contact_points.right)
        ):
            self._viz.set_primitive_geometry_transform(
                point.p,
                np.eye(3),
                f"p_{i}",
            )

            point_force = point.f * self._settings.contact_force_scaling

            # Copied from https://github.com/robotology/idyntree/pull/1087 until it is
            # available in conda

            position = point.p + point_force / 2  # the origin is in the cylinder center
            transform = np.zeros((4, 4))
            transform[0:3, 3] = position
            transform[3, 3] = 1
            transform[0:3, 0:3] = self._get_force_scaled_rotation(point_force)
            self._viz.viewer[f"f_{i}"].set_transform(transform)

        if save:
            image = self._viz.viewer.get_image()
            image.save(file_name_stem + ".png")

    def _get_force_scaled_rotation(self, point_force: np.ndarray) -> np.ndarray:
        force_norm = np.linalg.norm(point_force)
        scaling = np.diag([1, 1, force_norm])

        if force_norm < 1e-6:
            return scaling

        force_direction = point_force / force_norm
        cos_angle = np.dot(np.array([0, 0, 1]), force_direction)
        rotation_axis = self._skew(np.array([0, 0, 1])) @ force_direction

        if np.linalg.norm(rotation_axis) < 1e-6:
            return scaling

        skew_symmetric_matrix = self._skew(rotation_axis)
        rotation = (
            np.eye(3)
            + skew_symmetric_matrix
            + np.dot(skew_symmetric_matrix, skew_symmetric_matrix)
            * ((1 - cos_angle) / (np.linalg.norm(rotation_axis) ** 2))
        )
        return rotation @ scaling

    def _visualize_multiple_states(
        self,
        states: list[HumanoidState],
        timestep_s: float | list[float] | np.ndarray,
        time_multiplier: float,
        save: bool,
        file_name_stem: str,
    ):
        _timestep_s = copy.deepcopy(timestep_s)
        if (
            _timestep_s is None
            or isinstance(_timestep_s, float)
            or _timestep_s.size == 1
        ):
            single_step = _timestep_s if _timestep_s is not None else 0.0
            _timestep_s = [single_step] * len(states)

        if len(_timestep_s) != len(states):
            raise ValueError("timestep_s and states have different lengths.")

        folder_name = f"{self._settings.working_folder}/{file_name_stem}_frames"

        if save:
            pathlib.Path(folder_name).mkdir(parents=True, exist_ok=True)
            self._logger.info(
                f"Saving visualization frames in {folder_name}. "
                "Make sure to have the visualizer open, "
                "otherwise the process will hang."
            )

        for i, state in enumerate(states):
            self._logger.info(f"Visualizing state {i + 1}/{len(states)}")
            start = time.time()
            self._visualize_single_state(
                state,
                save=save,
                file_name_stem=f"{folder_name}/frame_{i:03}",
            )
            end = time.time()
            elapsed_s = end - start
            sleep_time = _timestep_s[i] * time_multiplier - elapsed_s
            time.sleep(max(0.0, sleep_time))

        if save:
            if timestep_s is None:
                self._logger.warning("timestep_s is None. Saving video with 1.0 fps.")
                fps = 1.0
            elif isinstance(timestep_s, list):
                if len(timestep_s) > 1:
                    self._logger.warning(
                        "The input timestep is a list. "
                        "Using the average to compute the fps."
                    )
                    fps = 1.0 / (sum(timestep_s) / len(timestep_s))
                else:
                    fps = 1.0 / timestep_s[0]
            elif isinstance(timestep_s, np.ndarray):
                if timestep_s.size > 1:
                    self._logger.warning(
                        "The input timestep is a list. "
                        "Using the average to compute the fps."
                    )
                    fps = 1.0 / (sum(timestep_s) / len(timestep_s))
                else:
                    fps = 1.0 / timestep_s
            elif isinstance(timestep_s, float):
                fps = 1.0 / timestep_s
            else:
                self._logger.warning("Using the fps=1.0")
                fps = 1.0

            frames = ffmpeg.input(
                f"{folder_name}/frame_*.png", pattern_type="glob", framerate=fps
            )
            video = ffmpeg.output(
                frames,
                f"{self._settings.working_folder}/{file_name_stem}.mp4",
                video_bitrate="20M",
            )
            ffmpeg.run(video)

    def visualize(
        self,
        state: HumanoidState | list[HumanoidState],
        timestep_s: float | list[float] | np.ndarray = None,
        time_multiplier: float = 1.0,
        save: bool = False,
        file_name_stem: str = "humanoid_state_visualization",
    ):
        if isinstance(state, list):
            self._visualize_multiple_states(
                state,
                timestep_s=timestep_s,
                time_multiplier=time_multiplier,
                save=save,
                file_name_stem=file_name_stem,
            )
        else:
            self._visualize_single_state(
                state, save=save, file_name_stem=file_name_stem
            )
