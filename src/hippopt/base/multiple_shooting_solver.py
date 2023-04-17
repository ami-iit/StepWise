import copy
import dataclasses
import itertools
from collections.abc import Iterator
from typing import Dict, List, Tuple

import casadi as cs
import numpy as np

from hippopt.integrators.implicit_trapezoid import ImplicitTrapezoid

from .dynamics import TDynamics
from .opti_solver import OptiSolver
from .optimal_control_solver import OptimalControlSolver
from .optimization_object import OptimizationObject, TimeExpansion, TOptimizationObject
from .optimization_solver import OptimizationSolver, TOptimizationSolver
from .problem import ExpressionType, Problem
from .single_step_integrator import SingleStepIntegrator, step


@dataclasses.dataclass
class MultipleShootingSolver(OptimalControlSolver):
    optimization_solver: dataclasses.InitVar[OptimizationSolver] = dataclasses.field(
        default=None
    )
    _optimization_solver: TOptimizationSolver = dataclasses.field(default=None)

    default_integrator: dataclasses.InitVar[SingleStepIntegrator] = dataclasses.field(
        default=None
    )
    _default_integrator: SingleStepIntegrator = dataclasses.field(default=None)
    _flattened_variables: List[
        Dict[str, Tuple[int, Iterator[cs.MX]]]
    ] = dataclasses.field(default=None)

    def __post_init__(
        self,
        optimization_solver: OptimizationSolver,
        default_integrator: SingleStepIntegrator,
    ):
        self._optimization_solver = (
            optimization_solver
            if isinstance(optimization_solver, OptimizationSolver)
            else OptiSolver()
        )

        self._default_integrator = (
            default_integrator
            if isinstance(default_integrator, SingleStepIntegrator)
            else ImplicitTrapezoid()
        )
        self._flattened_variables = []

    def generate_optimization_objects(
        self, input_structure: TOptimizationObject | List[TOptimizationObject], **kwargs
    ) -> TOptimizationObject | List[TOptimizationObject]:
        if isinstance(input_structure, list):
            output_list = []
            for element in input_structure:
                output_list.append(
                    self.generate_optimization_objects(element, **kwargs)
                )
            return output_list

        if "horizon" not in kwargs and "horizons" not in kwargs:
            return self._optimization_solver.generate_optimization_objects(
                input_structure=input_structure, **kwargs
            )

        default_horizon_length = int(1)
        if "horizon" in kwargs:
            default_horizon_length = int(kwargs["horizon"])
            if default_horizon_length < 1:
                raise ValueError(
                    "The specified horizon needs to be a strictly positive integer"
                )

        output = copy.deepcopy(input_structure)
        for field in dataclasses.fields(output):
            horizon_length = default_horizon_length

            constant = (
                OptimizationObject.TimeDependentField in field.metadata
                and not field.metadata[OptimizationObject.TimeDependentField]
            )

            custom_horizon = False
            if "horizons" in kwargs:
                horizons_dict = kwargs["horizons"]
                if isinstance(horizons_dict, dict) and field.name in horizons_dict:
                    constant = False
                    horizon_length = int(horizons_dict[field.name])
                    custom_horizon = True
                    if horizon_length < 1:
                        raise ValueError(
                            "The specified horizon for "
                            + field.name
                            + " needs to be a strictly positive integer"
                        )

            if constant:
                continue

            field_value = output.__getattribute__(field.name)

            expand_storage = (
                field.metadata[OptimizationObject.TimeExpansionField]
                is TimeExpansion.Matrix
                if OptimizationObject.TimeExpansionField in field.metadata
                else False
            )

            if OptimizationObject.StorageTypeField in field.metadata:
                if expand_storage:
                    if not isinstance(field_value, np.ndarray):
                        raise ValueError(
                            "Field "
                            + field.name
                            + "is not a Numpy array. Cannot expand it to the horizon."
                            ' Consider using "TimeExpansion.List" as time_expansion strategy.'
                        )

                    if field_value.ndim > 1 and field_value.shape[1] > 1:
                        raise ValueError(
                            "Cannot expand "
                            + field.name
                            + " since it is already a matrix."
                            ' Consider using "TimeExpansion.List" as time_expansion strategy.'
                        )
                    output.__setattr__(
                        field.name, np.zeros(field_value.shape[0], horizon_length)
                    )  # This is only needed to get the structure for the optimization variables.
                else:
                    output_value = []
                    for _ in range(horizon_length):
                        output_value.append(copy.deepcopy(field_value))

                    output.__setattr__(field.name, output_value)

                continue

            if (
                isinstance(field_value, OptimizationObject)
                or (
                    isinstance(field_value, list)
                    and all(
                        isinstance(elem, OptimizationObject) for elem in field_value
                    )
                )
            ) and (
                OptimizationObject.TimeDependentField
                in field.metadata  # If true, the field has to be true, see above
                or custom_horizon
            ):  # Nested variables are extended only if it is set as time dependent or if explicitly specified
                output_value = []
                for _ in range(horizon_length):
                    output_value.append(copy.deepcopy(field_value))

                output.__setattr__(field.name, output_value)

        variables = self._optimization_solver.generate_optimization_objects(
            input_structure=output, **kwargs
        )

        self._flattened_variables.append(
            self._generate_flatten_optimization_objects(object_in=variables)
        )

        return variables

    def _generate_flatten_optimization_objects(
        self,
        object_in: TOptimizationObject | List[TOptimizationObject],
        top_level: bool = True,
        base_string: str = "",
        base_iterator: Tuple[
            int, Iterator[TOptimizationObject | List[TOptimizationObject]]
        ] = None,
    ) -> Dict[str, Tuple[int, Iterator[cs.MX]]]:
        assert (bool(top_level) != bool(base_iterator is not None)) or (
            not top_level and base_iterator is None
        )  # Cannot be top level and have base iterator
        output = {}
        for field in dataclasses.fields(object_in):
            field_value = object_in.__getattribute__(field.name)

            time_dependent = (
                OptimizationObject.TimeDependentField in field.metadata
                and field.metadata[OptimizationObject.TimeDependentField]
                and top_level  # only top level variables can be time dependent
            )

            expand_storage = (
                field.metadata[OptimizationObject.TimeExpansionField]
                is TimeExpansion.Matrix
                if OptimizationObject.TimeExpansionField in field.metadata
                else False
            )

            # cases:
            # storage, time dependent or not,
            # aggregate, not time dependent (otherwise it would be a list),
            # list[aggregate], time dependent or not,
            # list[list of aggregate], but only if time dependent

            if OptimizationObject.StorageTypeField in field.metadata:  # storage
                if not time_dependent:
                    if base_iterator is not None:
                        new_generator = (
                            val.__getattribute__(field.name) for val in base_iterator[1]
                        )
                        output[base_string + field.name] = (
                            base_iterator[0],
                            new_generator,
                        )
                    else:
                        output[base_string + field.name] = (
                            1,
                            itertools.repeat(field_value),
                        )
                    continue

                if expand_storage:
                    n = field_value.shape[1]
                    output[base_string + field.name] = (
                        n,
                        (field_value[:, k] for k in range(n)),
                    )
                    continue

                assert isinstance(
                    field_value, list
                )  # time dependent and not expand_storage
                n = len(field_value)  # list case
                output[base_string + field.name] = (
                    n,
                    (field_value[k] for k in range(n)),
                )
                continue

            if isinstance(
                field_value, OptimizationObject
            ):  # aggregate (cannot be time dependent)
                generator = (
                    (val.__getattribute__(field.name) for val in base_iterator[1])
                    if base_iterator is not None
                    else None
                )

                output = output | self._generate_flatten_optimization_objects(
                    object_in=field_value,
                    top_level=False,
                    base_string=base_string + field.name + ".",
                    base_iterator=(base_iterator[0], generator)
                    if generator is not None
                    else None,
                )
                continue

            if isinstance(field_value, list) and all(
                isinstance(elem, OptimizationObject) for elem in field_value
            ):  # list[aggregate]
                if not time_dependent:
                    for k in range(len(field_value)):
                        generator = (
                            (
                                val.__getattribute__(field.name)[k]
                                for val in base_iterator[1]
                            )
                            if base_iterator is not None
                            else None
                        )
                        output = output | self._generate_flatten_optimization_objects(
                            object_in=field_value,
                            top_level=False,
                            base_string=base_string
                            + field.name
                            + "["
                            + str(k)
                            + "].",  # we flatten the list. Note the added [k]
                            base_iterator=(base_iterator[0], generator)
                            if generator is not None
                            else None,
                        )
                    continue
                # If we are time dependent (and hence top_level has to be true), there is no base generator
                generator = (val for val in field_value)
                for k in range(len(field_value)):
                    output = output | self._generate_flatten_optimization_objects(
                        object_in=field_value,
                        top_level=False,
                        base_string=base_string
                        + field.name
                        + ".",  # we don't flatten the list
                        base_iterator=(len(field_value), generator),
                    )
                continue

            if (
                isinstance(field_value, list)
                and time_dependent
                and all(isinstance(elem, list) for elem in field_value)
            ):  # list[list[aggregate]], only time dependent
                generator = (val for val in field_value)
                for k in range(len(field_value)):
                    output = output | self._generate_flatten_optimization_objects(
                        object_in=field_value,
                        top_level=False,
                        base_string=base_string
                        + field.name
                        + ".",  # we don't flatten the list
                        base_iterator=(len(field_value), generator),
                    )
                continue

        return output

    def get_optimization_objects(
        self,
    ) -> TOptimizationObject | List[TOptimizationObject]:
        return self._optimization_solver.get_optimization_objects()

    def register_problem(self, problem: Problem) -> None:
        self._optimization_solver.register_problem(problem)

    def get_problem(self) -> Problem:
        return self._optimization_solver.get_problem()

    def add_dynamics(
        self,
        dynamics: TDynamics,
        t0: cs.MX = cs.MX(0.0),
        mode: ExpressionType = ExpressionType.subject_to,
        **kwargs
    ) -> None:
        if "dt" not in kwargs:
            raise ValueError(
                "MultipleShootingSolver needs dt to be specified when adding a dynamics"
            )

        top_level_index = 0
        if isinstance(self.get_optimization_objects(), list):
            if "top_level_index" not in kwargs:
                raise ValueError(
                    "The optimization objects are in a list, but top_level_index has not been specified."
                )
            top_level_index = kwargs["top_level_index"]

        dt_in = kwargs["dt"]

        max_n = 0

        if "max_steps" in kwargs:
            max_n = kwargs["max_steps"]

            if not isinstance(max_n, int) or max_n < 2:
                raise ValueError(
                    "max_steps is specified, but it needs to be an integer greater than 1"
                )

        dt_size = 1

        if isinstance(dt_in, float):
            dt_generator = itertools.repeat(cs.MX(dt_in))
        elif isinstance(dt_in, str):
            if dt_in not in self._flattened_variables[top_level_index]:
                raise ValueError(
                    "The specified dt name is not found in the optimization variables"
                )
            dt_var_tuple = self._flattened_variables[top_level_index][dt_in]
            dt_size = dt_var_tuple[0]
            dt_generator = dt_var_tuple[1]
        else:
            raise ValueError("Unsupported dt type")

        dt_tuple = (dt_size, dt_generator)

        integrator = (
            kwargs["integrator"]
            if "integrator" in kwargs
            and isinstance(kwargs["integrator"], SingleStepIntegrator)
            else self._default_integrator
        )

        variables = {}
        n = max_n
        for var in dynamics.state_variables():
            if var not in self._flattened_variables[top_level_index]:
                raise ValueError(
                    "Variable " + var + " not found in the optimization variables."
                )
            var_tuple = self._flattened_variables[top_level_index][var]
            var_n = var_tuple[0]
            if n == 0:
                if var_n < 2:
                    raise ValueError(
                        "The state variable " + var + " is not time dependent."
                    )
                n = var_n

            if var_n < n:
                raise ValueError(
                    "The state variable " + var + " has a too short prediction horizon."
                )

            variables[var] = var_tuple

        if 1 < dt_tuple[0] < n:
            raise ValueError("The specified dt has a too small prediction horizon.")

        additional_inputs = {}
        for inp in dynamics.input_names():
            if inp not in self._flattened_variables[top_level_index]:
                raise ValueError(
                    "Variable " + inp + " not found in the optimization variables."
                )

            if inp not in variables:
                inp_tuple = self._flattened_variables[top_level_index][inp]

                inp_n = inp_tuple[0]

                if 1 < inp_n < n:
                    raise ValueError(
                        "The input "
                        + inp
                        + " is time dependent, but it has a too small prediction horizon."
                    )

                additional_inputs[inp] = inp_tuple

        x_k = {name: next(var_tuple[1]) for name, var_tuple in variables}
        u_k = {name: next(inp_tuple[1]) for name, inp_tuple in additional_inputs}

        for i in range(n - 1):
            x_next = {name: next(var_tuple[1]) for name, var_tuple in variables}
            u_next = {name: next(inp_tuple[1]) for name, inp_tuple in additional_inputs}
            dt = next(dt_tuple[1])
            integrated = step(
                integrator,
                dynamics=dynamics,
                x0=x_k | u_k,
                xf=x_next | u_next,
                dt=dt,
                t0=t0 + cs.MX(i) * dt,
            )

            # In the following, we add the dynamics expressions through the problem interface, rather than the
            # solver interface. In this way, we can exploit the machinery handling the generators,
            # and we can switch the dynamics from constraints to costs
            self.get_problem().add_expression(
                mode=mode,
                expression=(cs.MX(val == integrated[name]) for name, val in x_next),
                **kwargs
            )

            x_k = x_next
            u_k = u_next

    def set_initial_guess(
        self, initial_guess: TOptimizationObject | List[TOptimizationObject]
    ):
        self._optimization_solver.set_initial_guess(initial_guess=initial_guess)

    def solve(self) -> None:
        self._optimization_solver.solve()

    def get_values(self) -> TOptimizationObject | List[TOptimizationObject] | None:
        return self._optimization_solver.get_values()

    def get_cost_value(self) -> float | None:
        return self._optimization_solver.get_cost_value()

    def add_cost(self, input_cost: cs.MX):
        self._optimization_solver.add_cost(input_cost=input_cost)

    def add_constraint(self, input_constraint: cs.MX):
        self._optimization_solver.add_constraint(input_constraint=input_constraint)

    def cost_function(self) -> cs.MX:
        return self._optimization_solver.cost_function()
