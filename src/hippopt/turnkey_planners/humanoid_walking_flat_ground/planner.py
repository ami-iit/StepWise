import copy
import dataclasses
import typing

import adam.casadi
import casadi as cs
import numpy as np

import hippopt as hp
import hippopt.integrators as hp_int
import hippopt.robot_planning as hp_rp


@dataclasses.dataclass
class ExtendedContactPoint(hp_rp.ContactPoint):
    u_v: hp.StorageType = hp.default_storage_field(hp.Variable)

    def __post_init__(self, input_descriptor: hp_rp.ContactPointDescriptor) -> None:
        super().__post_init__(input_descriptor)
        self.u_v = np.zeros(3)


@dataclasses.dataclass
class FeetContactPoints(hp.OptimizationObject):
    left: list[ExtendedContactPoint] = hp.default_composite_field()
    right: list[ExtendedContactPoint] = hp.default_composite_field()


@dataclasses.dataclass
class FeetContactPointDescriptors:
    left: list[hp_rp.ContactPointDescriptor] = dataclasses.field(default_factory=list)
    right: list[hp_rp.ContactPointDescriptor] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Settings:
    robot_urdf: str = dataclasses.field(default=None)
    joints_name_list: list[str] = dataclasses.field(default=None)
    contact_points: FeetContactPointDescriptors = dataclasses.field(default=None)
    root_link: str = dataclasses.field(default=None)
    gravity: np.array = dataclasses.field(default=None)
    horizon_length: int = dataclasses.field(default=None)
    integrator: typing.Type[hp.SingleStepIntegrator] = dataclasses.field(default=None)
    terrain: hp_rp.TerrainDescriptor = dataclasses.field(default=None)
    planar_dcc_height_multiplier: float = dataclasses.field(default=None)
    dcc_gain: float = dataclasses.field(default=None)
    dcc_epsilon: float = dataclasses.field(default=None)
    static_friction: float = dataclasses.field(default=None)
    maximum_velocity_control: np.ndarray = dataclasses.field(default=None)
    maximum_force_derivative: np.ndarray = dataclasses.field(default=None)
    maximum_angular_momentum: float = dataclasses.field(default=None)
    minimum_com_height: float = dataclasses.field(default=None)
    minimum_feet_lateral_distance: float = dataclasses.field(default=None)
    maximum_feet_relative_height: float = dataclasses.field(default=None)

    casadi_function_options: dict = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        self.casadi_function_options = (
            self.casadi_function_options
            if isinstance(self.casadi_function_options, dict)
            else {}
        )
        self.root_link = "root_link"
        self.gravity = np.array([0.0, 0.0, -9.80665, 0.0, 0.0, 0.0])
        self.integrator = hp_int.ImplicitTrapezoid
        self.terrain = hp_rp.PlanarTerrain()
        self.planar_dcc_height_multiplier = 10.0
        self.dcc_gain = 20.0
        self.dcc_epsilon = 0.05
        self.static_friction = 0.3
        self.maximum_velocity_control = np.ndarray([2.0, 2.0, 5.0])
        self.maximum_force_derivative = np.ndarray([100.0, 100.0, 100.0])
        self.maximum_angular_momentum = 10.0
        self.minimum_com_height = 0.1
        self.minimum_feet_lateral_distance = 0.1
        self.maximum_feet_relative_height = 0.05

    def is_valid(self) -> bool:
        return (
            self.robot_urdf is not None
            and self.joints_name_list is not None
            and self.contact_points is not None
            and self.horizon_length is not None
        )


@dataclasses.dataclass
class Variables(hp.OptimizationObject):
    contact_points: FeetContactPoints | list[
        FeetContactPoints
    ] = hp.default_composite_field()
    com: hp.StorageType = hp.default_storage_field(hp.Variable)
    centroidal_momentum: hp.StorageType = hp.default_storage_field(hp.Variable)
    mass: hp.StorageType = hp.default_storage_field(hp.Parameter)
    kinematics: hp_rp.FloatingBaseSystem = hp.default_composite_field()

    com_initial: hp.StorageType = hp.default_storage_field(hp.Parameter)
    centroidal_momentum_initial: hp.StorageType = hp.default_storage_field(hp.Parameter)

    dt: hp.StorageType = hp.default_storage_field(hp.Parameter)
    gravity: hp.StorageType = hp.default_storage_field(hp.Parameter)
    planar_dcc_height_multiplier: hp.StorageType = hp.default_storage_field(
        hp.Parameter
    )
    dcc_gain: hp.StorageType = hp.default_storage_field(hp.Parameter)
    dcc_epsilon: hp.StorageType = hp.default_storage_field(hp.Parameter)
    static_friction: hp.StorageType = hp.default_storage_field(hp.Parameter)
    maximum_velocity_control: hp.StorageType = hp.default_storage_field(hp.Parameter)
    maximum_force_derivative: hp.StorageType = hp.default_storage_field(hp.Parameter)
    maximum_angular_momentum: hp.StorageType = hp.default_storage_field(hp.Parameter)
    minimum_com_height: hp.StorageType = hp.default_storage_field(hp.Parameter)
    minimum_feet_lateral_distance: hp.StorageType = hp.default_storage_field(
        hp.Parameter
    )
    maximum_feet_relative_height: hp.StorageType = hp.default_storage_field(
        hp.Parameter
    )

    settings: dataclasses.InitVar[Settings] = dataclasses.field(default=None)
    kin_dyn_object: dataclasses.InitVar[
        adam.casadi.KinDynComputations
    ] = dataclasses.field(default=None)

    def __post_init__(
        self,
        settings: Settings,
        kin_dyn_object: adam.casadi.KinDynComputations,
    ) -> None:
        self.contact_points.left = [
            hp_rp.ContactPoint(descriptor=point)
            for point in settings.contact_points.left
        ]
        self.contact_points.right = [
            hp_rp.ContactPoint(descriptor=point)
            for point in settings.contact_points.right
        ]

        self.com = np.zeros(3)
        self.centroidal_momentum = np.zeros(6)
        self.kinematics = hp_rp.FloatingBaseSystem(kin_dyn_object.NDoF)
        self.dt = 0.1
        self.gravity = kin_dyn_object.g[:, 3]
        self.mass = kin_dyn_object.get_total_mass()

        self.com_initial = np.zeros(3)
        self.centroidal_momentum_initial = np.zeros(6)

        self.planar_dcc_height_multiplier = settings.planar_dcc_height_multiplier
        self.dcc_gain = settings.dcc_gain
        self.dcc_epsilon = settings.dcc_epsilon
        self.static_friction = settings.static_friction
        self.maximum_velocity_control = settings.maximum_velocity_control
        self.maximum_force_derivative = settings.maximum_force_derivative
        self.maximum_angular_momentum = settings.maximum_angular_momentum
        self.minimum_com_height = settings.minimum_com_height
        self.minimum_feet_lateral_distance = settings.minimum_feet_lateral_distance
        self.maximum_feet_relative_height = settings.maximum_feet_relative_height


class HumanoidWalkingFlatGround:
    def __init__(self, settings: Settings) -> None:
        if not settings.is_valid():
            raise ValueError("Settings are not valid")
        self.settings = copy.deepcopy(settings)
        self.kin_dyn_object = adam.casadi.KinDynComputations(
            urdfstring=self.settings.robot_urdf,
            joints_name_list=self.settings.joints_name_list,
            root_link=self.settings.root_link,
            gravity=self.settings.gravity,
            f_opts=self.settings.casadi_function_options,
        )

        self.variables = Variables(
            settings=self.settings, kin_dyn_object=self.kin_dyn_object
        )

        self.ocp = hp.OptimalControlProblem.create(
            self.variables, horizon_length=self.settings.horizon_length
        )

        problem = self.ocp.problem
        sym = self.ocp.symbolic_structure

        default_integrator = self.settings.integrator

        function_inputs = {
            "mass_name": sym.mass.name(),
            "momentum_name": sym.centroidal_momentum.name(),
            "com_name": sym.com.name(),
            "quaternion_xyzw_name": "q",
            "gravity_name": sym.gravity.name(),
            "point_position_names": [],
            "point_force_names": [],
            "point_position_in_frame_name": "p_parent",
            "base_position_name": "pb",
            "base_quaternion_xyzw_name": "qb",
            "joint_positions_name": "s",
            "base_position_derivative_name": "pb_dot",
            "base_quaternion_xyzw_derivative_name": "qb_dot",
            "joint_velocities_name": "s_dot",
            "point_position_name": "p",
            "point_force_name": "f",
            "point_velocity_name": "v",
            "point_force_derivative_name": "f_dot",
            "point_position_control_name": "u_p",
            "height_multiplier_name": "kt",
            "dcc_gain_name": "k_bs",
            "dcc_epsilon_name": "eps",
            "static_friction_name": "mu_s",
            "options": self.settings.casadi_function_options,
        }

        # Normalized quaternion computation
        normalized_quaternion_fun = hp_rp.quaternion_xyzw_normalization(
            **function_inputs
        )
        normalized_quaternion = normalized_quaternion_fun(
            q=sym.kinematics.base.quaternion_xyzw
        )["quaternion_normalized"]

        # Align names used in the terrain function with those in function_inputs
        self.settings.terrain.change_options(**function_inputs)

        # Definition of contact constraint functions
        dcc_planar_fun = hp_rp.dcc_planar_complementarity(
            terrain=self.settings.terrain,
            **function_inputs,
        )
        dcc_margin_fun = hp_rp.dcc_complementarity_margin(
            terrain=self.settings.terrain,
            **function_inputs,
        )
        friction_margin_fun = hp_rp.friction_cone_square_margin(
            terrain=self.settings.terrain, **function_inputs
        )
        height_fun = self.settings.terrain.height_function()
        normal_force_fun = hp_rp.normal_force_component(
            terrain=self.settings.terrain, **function_inputs
        )

        point_kinematics_functions = {}

        for point in sym.contact_points.left + sym.contact_points.right:
            # dot(f) = f_dot
            problem.add_dynamics(
                hp.dot(point.f) == point.f_dot,
                x0=point.f0,
                integrator=default_integrator,
                name=point.f.name() + "_dynamics",
            )

            # dot(p) = v
            problem.add_dynamics(
                hp.dot(point.p) == point.v,
                x0=point.p0,
                integrator=default_integrator,
                name=point.p.name() + "_dynamics",
            )

            # Planar complementarity
            dcc_planar = dcc_planar_fun(
                p=point.p, kt=sym.planar_dcc_height_multiplier, u_p=point.u_v
            )["planar_complementarity"]
            problem.add_expression_to_horizon(
                expression=cs.MX(point.v == dcc_planar),
                apply_to_first_elements=True,
                name=point.p.name() + "_planar_complementarity",
            )

            # Normal complementarity
            dcc_margin = dcc_margin_fun(
                p=point.p,
                f=point.f,
                v=point.v,
                f_dot=point.f_dot,
                k_bs=sym.dcc_gain,
                eps=sym.dcc_epsilon,
            )["dcc_complementarity_margin"]
            problem.add_expression_to_horizon(
                expression=cs.MX(dcc_margin >= 0),
                apply_to_first_elements=True,
                name=point.p.name() + "_dcc",
            )

            # Point height greater than zero
            point_height = height_fun(p=point.p)["point_height"]
            problem.add_expression_to_horizon(
                expression=cs.MX(point_height >= 0),
                apply_to_first_elements=False,
                name=point.p.name() + "_height",
            )

            # Normal force greater than zero
            normal_force = normal_force_fun(p=point.p, f=point.f)["normal_force"]
            problem.add_expression_to_horizon(
                expression=cs.MX(normal_force >= 0),
                apply_to_first_elements=False,
                name=point.f.name() + "_normal",
            )

            # Friction
            friction_margin = friction_margin_fun(
                p=point.p,
                f=point.f,
                mu_s=sym.static_friction,
            )["friction_cone_square_margin"]
            problem.add_expression_to_horizon(
                expression=cs.MX(friction_margin >= 0),
                apply_to_first_elements=False,
                name=point.f.name() + "_friction",
            )

            # Bounds on contact velocity control inputs
            problem.add_expression_to_horizon(
                expression=cs.Opti_bounded(
                    -sym.maximum_velocity_control,
                    point.u_v,
                    sym.maximum_velocity_control,
                ),
                apply_to_first_elements=True,
                name=point.u_v.name() + "_bounds",
            )

            # Bounds on contact force control inputs
            problem.add_expression_to_horizon(
                expression=cs.Opti_bounded(
                    -sym.maximum_force_control,
                    point.u_f,
                    sym.maximum_force_control,
                ),
                apply_to_first_elements=True,
                name=point.u_f.name() + "_bounds",
            )

            # Creation of contact kinematics consistency functions
            descriptor = point.descriptor
            if descriptor.foot_frame not in point_kinematics_functions:
                point_kinematics_functions[
                    descriptor.foot_frame
                ] = hp_rp.point_position_from_kinematics(
                    kindyn_object=self.kin_dyn_object,
                    frame_name=descriptor.foot_frame,
                    **function_inputs,
                )

            # Consistency between the contact position and the kinematics
            point_kinematics = point_kinematics_functions[descriptor.foot_frame](
                pb=sym.kinematics.base.position,
                qb=normalized_quaternion,
                s=sym.kinematics.joints.positions,
                p_parent=descriptor.position_in_foot_frame,
            )["point_position"]

            problem.add_expression_to_horizon(
                expression=cs.MX(point.p == point_kinematics),
                apply_to_first_elements=False,
                name=point.p.name() + "_kinematics_consistency",
            )

            function_inputs["point_position_names"].append(point.p.name())
            function_inputs["point_force_names"].append(point.f.name())

        # dot(pb) = pb_dot (base position dynamics)
        problem.add_dynamics(
            hp.dot(sym.kinematics.base.position) == sym.kinematics.base.linear_velocity,
            x0=sym.kinematics.base.initial_position,
            integrator=default_integrator,
            name="base_position_dynamics",
        )

        # dot(q) = q_dot (base quaternion dynamics)
        problem.add_dynamics(
            hp.dot(sym.kinematics.base.quaternion_xyzw)
            == sym.kinematics.base.quaternion_velocity_xyzw,
            x0=sym.kinematics.base.initial_quaternion_xyzw,
            integrator=default_integrator,
            name="base_quaternion_dynamics",
        )

        # dot(s) = s_dot (joint position dynamics)
        problem.add_dynamics(
            hp.dot(sym.kinematics.joints.positions) == sym.kinematics.joints.velocities,
            x0=sym.kinematics.joints.initial_positions,
            integrator=default_integrator,
            name="joint_position_dynamics",
        )

        # dot(com) = h_g[:3]/m (center of mass dynamics)
        com_dynamics = hp_rp.com_dynamics_from_momentum(**function_inputs)
        problem.add_dynamics(
            hp.dot(sym.com) == com_dynamics,
            x0=sym.com_initial,
            integrator=default_integrator,
            name="com_dynamics",
        )

        # dot(h) = sum_i (p_i x f_i) + mg (centroidal momentum dynamics)
        centroidal_dynamics = hp_rp.centroidal_dynamics_with_point_forces(
            number_of_points=len(function_inputs["point_position_names"]),
            **function_inputs,
        )
        problem.add_dynamics(
            hp.dot(sym.centroidal_momentum) == centroidal_dynamics,
            x0=sym.centroidal_momentum_initial,
            integrator=default_integrator,
            name="centroidal_momentum_dynamics",
        )

        # Unitary quaternion
        problem.add_expression_to_horizon(
            expression=cs.MX(cs.sumsqr(sym.kinematics.base.quaternion_xyzw) == 1),
            apply_to_first_elements=False,
            name="unitary_quaternion",
        )

        # Consistency of com position with kinematics
        com_kinematics_fun = hp_rp.center_of_mass_position_from_kinematics(
            kindyn_object=self.kin_dyn_object, **function_inputs
        )
        com_kinematics = com_kinematics_fun(
            pb=sym.kinematics.base.position,
            qb=normalized_quaternion,
            s=sym.kinematics.joints.positions,
        )["com_position"]
        problem.add_expression_to_horizon(
            expression=cs.MX(sym.com == com_kinematics),
            apply_to_first_elements=False,
            name="com_kinematics_consistency",
        )

        # Consistency of centroidal momentum (angular part only) with kinematics
        centroidal_kinematics_fun = hp_rp.centroidal_momentum_from_kinematics(
            kindyn_object=self.kin_dyn_object, **function_inputs
        )
        centroidal_kinematics = centroidal_kinematics_fun(
            pb=sym.kinematics.base.position,
            qb=normalized_quaternion,
            s=sym.kinematics.joints.positions,
            pb_dot=sym.kinematics.base.linear_velocity,
            qb_dot=sym.kinematics.base.quaternion_velocity_xyzw,
            s_dot=sym.kinematics.joints.velocities,
        )["h_g"]
        problem.add_expression_to_horizon(
            expression=cs.MX(sym.centroidal_momentum[3:] == centroidal_kinematics[3:]),
            apply_to_first_elements=True,
            name="centroidal_momentum_kinematics_consistency",
        )

        # Bounds on angular momentum
        problem.add_expression_to_horizon(
            expression=cs.Opti_bounded(
                -sym.maximum_angular_momentum,
                sym.centroidal_momentum[3:],
                sym.maximum_angular_momentum,
            ),
            apply_to_first_elements=True,
            name="angular_momentum_bounds",
        )

        # Minimum com height
        com_height = height_fun(p=sym.com)["point_height"]
        problem.add_expression_to_horizon(
            expression=cs.MX(com_height >= sym.minimum_com_height),
            apply_to_first_elements=False,
            name="minimum_com_height",
        )

        # Minimum feet lateral distance
        left_frame = sym.contact_points.left[0].descriptor.foot_frame
        right_frame = sym.contact_points.right[0].descriptor.foot_frame
        relative_position_fun = hp_rp.frames_relative_position(
            kindyn_object=self.kin_dyn_object,
            reference_frame=right_frame,
            target_frame=left_frame,
            **function_inputs,
        )
        relative_position = relative_position_fun(s=sym.kinematics.joints.positions)[
            "relative_position"
        ]
        problem.add_expression_to_horizon(
            expression=cs.MX(
                relative_position[:2] >= sym.minimum_feet_lateral_distance
            ),
            apply_to_first_elements=False,
            name="minimum_feet_distance",
        )

        # Maximum feet relative height
        def get_centroid(
            points: list[ExtendedContactPoint], function_inputs_dict: dict
        ) -> cs.MX:
            function_inputs_dict["point_position_names"] = [
                pt.p.name() for pt in points
            ]
            point_position_dict = {pt.p.name(): pt.p for pt in points}
            centroid_fun = hp_rp.contact_points_centroid(
                number_of_points=len(function_inputs_dict["point_position_names"]),
                **function_inputs_dict,
            )
            return centroid_fun(**point_position_dict)["centroid"]

        left_centroid = get_centroid(
            points=sym.contact_points.left, function_inputs_dict=function_inputs
        )
        right_centroid = get_centroid(
            points=sym.contact_points.right, function_inputs_dict=function_inputs
        )
        problem.add_expression_to_horizon(
            expression=cs.Opti_bounded(
                -sym.maximum_feet_relative_height,
                (left_centroid[2] - right_centroid[2]),
                sym.maximum_feet_relative_height,
            ),
            apply_to_first_elements=False,
            name="maximum_feet_relative_height",
        )

    def set_initial_conditions(self) -> None:  # TODO: fill
        pass
