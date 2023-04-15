import abc
import copy
import dataclasses
from enum import Enum
from typing import Any, ClassVar, Type, TypeVar

import casadi as cs
import numpy as np

TOptimizationObject = TypeVar("TOptimizationObject", bound="OptimizationObject")
StorageType = cs.MX | np.ndarray


class TimeExpansion(Enum):
    List = 0
    Matrix = 1


@dataclasses.dataclass
class OptimizationObject(abc.ABC):
    StorageTypeValue: ClassVar[str] = "generic"
    StorageTypeField: ClassVar[str] = "StorageType"
    TimeDependentField: ClassVar[str] = "TimeDependent"
    TimeExpansionField: ClassVar[str] = "TimeExpansion"
    StorageTypeMetadata: ClassVar[dict[str, Any]] = dict(
        StorageType=StorageType, TimeDependent=False, TimeExpansion=TimeExpansion.List
    )

    @classmethod
    def default_storage_field(cls, **kwargs):
        pass

    # TODO Stefano: how to deal with the case where the field is a list of objects?
    def get_default_initialization(
        self: TOptimizationObject, field_name: str
    ) -> np.ndarray:
        """
        Get the default initialization of a given field
        It is supposed to be called only for the fields having the StorageType metadata
        """
        return np.zeros(dataclasses.asdict(self)[field_name].shape)

    def get_default_initialized_object(
        self: TOptimizationObject,
    ) -> TOptimizationObject:
        """
        :return: A copy of the object with its initial values
        """

        output = copy.deepcopy(self)

        for field in dataclasses.fields(output):
            if self.StorageTypeField in field.metadata:
                output.__setattr__(
                    field.name, output.get_default_initialization(field.name)
                )
                continue

            if isinstance(output.__getattribute__(field.name), OptimizationObject):
                output.__setattr__(
                    field.name,
                    output.__getattribute__(
                        field.name
                    ).get_default_initialized_object(),
                )

        return output


def default_storage_field(cls: Type[OptimizationObject], **kwargs):
    return cls.default_storage_field(**kwargs)


def time_varying_metadata(time_varying: bool = True):
    return {OptimizationObject.TimeDependentField: time_varying}
