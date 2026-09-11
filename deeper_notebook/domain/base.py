from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Type, TypeVar, Union, cast

from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from deeper_notebook.database.repository import (
    ensure_record_id,
    repo_create,
    repo_delete,
    repo_query,
    repo_relate,
    repo_update,
    repo_upsert,
)
from deeper_notebook.exceptions import (
    DatabaseOperationError,
    InvalidInputError,
    NotFoundError,
)

T = TypeVar("T", bound="ObjectModel")


class ObjectModel(BaseModel):
    id: Optional[str] = None
    table_name: ClassVar[str] = ""
    nullable_fields: ClassVar[set[str]] = set()  # Fields that can be saved as None
    created: Optional[datetime] = None
    updated: Optional[datetime] = None

    @classmethod
    async def get_all(
        cls: type[T],
        order_by=None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[T]:
        """Fetch all rows of this model's table.

        v0.7.159 — Optional `limit` and `offset` (SurrealQL `LIMIT … START …`)
        let callers paginate. Previously this returned the entire table
        unconditionally; `GET /notes` without a notebook filter, for example,
        loaded every note (with content) into memory and serialized it to
        JSON on a single request — multi-MB responses with hundreds of
        notes, blocking the API for seconds. New callers should pass
        `limit=` from the router; older callers that pass nothing keep
        the previous unbounded semantics for backward compatibility.

        Args:
            order_by: optional `"field"`, `"field direction"`, or
                comma-separated multi-clause string. Validated against
                `[a-z_][a-z0-9_]*` field names and `{asc, desc}` directions.
            limit: optional positive int for SurrealQL `LIMIT`.
            offset: optional non-negative int for SurrealQL `START`.

        Raises:
            InvalidInputError: if `limit` or `offset` are not valid ints
                in the allowed range. Raised BEFORE the database call so
                the FastAPI exception handler maps it to HTTP 400 instead
                of the generic 500 the outer `except` would produce.
        """
        # v0.7.159 — Validate pagination args before entering the try
        # block so the InvalidInputError propagates cleanly to the
        # FastAPI exception handler (mapped to HTTP 400). If it were
        # raised inside the try, the catch-all below would re-wrap it
        # as DatabaseOperationError → HTTP 500 — wrong status for what
        # is plainly a client-side input error.
        # `bool` is a subclass of int in Python; reject it explicitly so
        # `limit=True` doesn't silently become `LIMIT 1`.
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise InvalidInputError(f"limit must be a positive int, got {limit!r}")
        if offset is not None:
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise InvalidInputError(
                    f"offset must be a non-negative int, got {offset!r}"
                )

        try:
            # If called from a specific subclass, use its table_name
            if cls.table_name:
                target_class = cls
                table_name = cls.table_name
            else:
                # This path is taken if called directly from ObjectModel
                raise InvalidInputError(
                    "get_all() must be called from a specific model class"
                )
            if order_by:
                # Validate order_by to prevent SurrealQL injection
                # Supports: "field", "field direction", "field1 direction, field2 direction"
                import re

                allowed_field_pattern = re.compile(r"^[a-z_][a-z0-9_]*$")
                allowed_directions = {"asc", "desc"}

                clauses = [c.strip() for c in order_by.split(",")]
                validated_clauses = []
                for clause in clauses:
                    parts = clause.strip().split()
                    if len(parts) == 1:
                        if not allowed_field_pattern.match(parts[0].lower()):
                            raise InvalidInputError(
                                f"Invalid order_by field: '{parts[0]}'"
                            )
                        validated_clauses.append(parts[0].lower())
                    elif len(parts) == 2:
                        if (
                            not allowed_field_pattern.match(parts[0].lower())
                            or parts[1].lower() not in allowed_directions
                        ):
                            raise InvalidInputError(
                                f"Invalid order_by clause: '{clause.strip()}'"
                            )
                        validated_clauses.append(
                            f"{parts[0].lower()} {parts[1].lower()}"
                        )
                    else:
                        raise InvalidInputError(
                            f"Invalid order_by clause: '{clause.strip()}'"
                        )

                validated_order_by = ", ".join(validated_clauses)
                query = f"SELECT * FROM {table_name} ORDER BY {validated_order_by}"  # nosec B608
            else:
                query = f"SELECT * FROM {table_name}"  # nosec B608

            # v0.7.159 — Append LIMIT … START … only when the caller asked.
            # Inputs were already validated at the top of the method (must
            # be positive int / non-negative int respectively), so we can
            # interpolate without re-checking. SurrealQL is more permissive
            # than SQL about literals; the up-front type+range check is the
            # injection guard.
            if limit is not None:
                query = f"{query} LIMIT {limit}"
            if offset is not None:
                query = f"{query} START {offset}"

            result = await repo_query(query)
            objects = []
            for obj in result:
                try:
                    objects.append(target_class(**obj))
                except Exception as e:
                    logger.critical(f"Error creating object: {str(e)}")

            return objects
        except Exception as e:
            logger.error(f"Error fetching all {cls.table_name}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    @classmethod
    async def get(cls: type[T], id: str) -> T:
        if not id:
            raise InvalidInputError("ID cannot be empty")
        try:
            # Get the table name from the ID (everything before the first colon)
            table_name = id.split(":")[0] if ":" in id else id

            # If we're calling from a specific subclass and IDs match, use that class
            if cls.table_name and cls.table_name == table_name:
                target_class: type[T] = cls
            else:
                # Otherwise, find the appropriate subclass based on table_name
                found_class = cls._get_class_by_table_name(table_name)
                if not found_class:
                    raise InvalidInputError(f"No class found for table {table_name}")
                target_class = cast(type[T], found_class)

            result = await repo_query("SELECT * FROM $id", {"id": ensure_record_id(id)})
            if result:
                return target_class(**result[0])
            else:
                raise NotFoundError(f"{table_name} with id {id} not found")
        except Exception as e:
            logger.error(f"Error fetching object with id {id}: {str(e)}")
            logger.exception(e)
            raise NotFoundError(f"Object with id {id} not found - {str(e)}")

    @classmethod
    def _get_class_by_table_name(cls, table_name: str) -> Optional[type["ObjectModel"]]:
        """Find the appropriate subclass based on table_name."""

        def get_all_subclasses(c: type["ObjectModel"]) -> list[type["ObjectModel"]]:
            all_subclasses: list[type["ObjectModel"]] = []
            for subclass in c.__subclasses__():
                all_subclasses.append(subclass)
                all_subclasses.extend(get_all_subclasses(subclass))
            return all_subclasses

        for subclass in get_all_subclasses(ObjectModel):
            if hasattr(subclass, "table_name") and subclass.table_name == table_name:
                return subclass
        return None

    async def save(self) -> None:
        """
        Save the model to the database.

        Note: Embedding is no longer generated inline. Subclasses that need
        embedding should override save() to submit the appropriate embed_*
        command after calling super().save().
        """
        try:
            self.model_validate(self.model_dump(), strict=True)
            data = self._prepare_save_data()
            # SurrealDB datetime fields require native, timezone-aware datetime
            # values. API response conversion to ISO strings happens at the
            # router/service boundary via api.utils.iso.
            data["updated"] = datetime.now(timezone.utc)

            repo_result: list[dict[str, Any]] | dict[str, Any]
            if self.id is None:
                data["created"] = datetime.now(timezone.utc)
                repo_result = await repo_create(self.__class__.table_name, data)
            else:
                created = self.created
                if isinstance(created, datetime) and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                data["created"] = created
                logger.debug(f"Updating record with id {self.id}")
                repo_result = await repo_update(
                    self.__class__.table_name, self.id, data
                )
            # Update the current instance with the result
            # repo_result is a list of dictionaries
            result_list: list[dict[str, Any]] = (
                repo_result if isinstance(repo_result, list) else [repo_result]
            )
            for key, value in result_list[0].items():
                if hasattr(self, key):
                    if isinstance(getattr(self, key), BaseModel):
                        setattr(self, key, type(getattr(self, key))(**value))
                    else:
                        setattr(self, key, value)

        except ValidationError as e:
            logger.error(f"Validation failed: {e}")
            raise
        except RuntimeError:
            # Transaction conflicts should propagate for retry
            raise
        except Exception as e:
            logger.error(f"Error saving record: {e}")
            raise DatabaseOperationError(e)

    def _prepare_save_data(self) -> dict[str, Any]:
        data = self.model_dump()
        return {
            key: value
            for key, value in data.items()
            if value is not None or key in self.__class__.nullable_fields
        }

    async def delete(self) -> bool:
        if self.id is None:
            raise InvalidInputError("Cannot delete object without an ID")
        try:
            logger.debug(f"Deleting record with id {self.id}")
            # v0.8.125 — found live: the driver returns the deleted RecordID,
            # which is not JSON-serialisable; a router that echoed it back
            # 500'd AFTER the row was already gone. Honour the `-> bool`.
            return bool(await repo_delete(self.id))
        except Exception as e:
            logger.error(
                f"Error deleting {self.__class__.table_name} with id {self.id}: {str(e)}"
            )
            raise DatabaseOperationError(
                f"Failed to delete {self.__class__.table_name}"
            )

    async def relate(
        self, relationship: str, target_id: str, data: Optional[dict] = {}
    ) -> Any:
        if not relationship or not target_id or not self.id:
            raise InvalidInputError("Relationship and target ID must be provided")
        try:
            return await repo_relate(
                source=self.id, relationship=relationship, target=target_id, data=data
            )
        except Exception as e:
            logger.error(f"Error creating relationship: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    @field_validator("created", "updated", mode="before")
    @classmethod
    def parse_datetime(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id_to_str(cls, value):
        """v0.8.66 (audit D-6) — coerce a RecordID (or any non-str id) to a
        string for EVERY ObjectModel subclass, so a DB-sourced id round-trips
        uniformly into the `id: Optional[str]` contract. Previously only Source
        defended this; the other 7 models relied on the caller having run
        `parse_record_ids` first — a latent foot-gun that raised a Pydantic
        validation error the moment a raw RecordID reached a model constructor."""
        if value is None or value == "":
            return None
        return str(value)


class RecordModel(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        arbitrary_types_allowed=True,
        extra="allow",
        from_attributes=True,
        defer_build=True,
    )

    record_id: ClassVar[str]
    auto_save: ClassVar[bool] = (
        False  # Default to False, can be overridden in subclasses
    )
    _instances: ClassVar[dict[str, "RecordModel"]] = {}  # Store instances by record_id

    def __new__(cls, **kwargs):
        # If an instance already exists for this record_id, return it
        if cls.record_id in cls._instances:
            instance = cls._instances[cls.record_id]
            # Update instance with any new kwargs if provided
            if kwargs:
                for key, value in kwargs.items():
                    setattr(instance, key, value)
            return instance

        # If no instance exists, create a new one
        instance = super().__new__(cls)
        cls._instances[cls.record_id] = instance
        return instance

    def __init__(self, **kwargs):
        # Only initialize if this is a new instance
        if not hasattr(self, "_initialized"):
            object.__setattr__(self, "__dict__", {})

            # For RecordModel, we need to handle async initialization differently
            # Initialize with provided kwargs only for now
            super().__init__(**kwargs)

            # Mark as initialized but not loaded from DB yet
            object.__setattr__(self, "_initialized", True)
            object.__setattr__(self, "_db_loaded", False)

    async def _load_from_db(self):
        """Load data from database if not already loaded"""
        if not getattr(self, "_db_loaded", False):
            result = await repo_query(
                "SELECT * FROM ONLY $record_id",
                {"record_id": ensure_record_id(self.record_id)},
            )

            # Handle case where record doesn't exist yet
            if result:
                if isinstance(result, list) and len(result) > 0:
                    # Standard list response
                    row = result[0]
                    if isinstance(row, dict):
                        for key, value in row.items():
                            if hasattr(self, key):
                                object.__setattr__(self, key, value)
                elif isinstance(result, dict):
                    # Direct dict response
                    for key, value in result.items():
                        if hasattr(self, key):
                            object.__setattr__(self, key, value)

            object.__setattr__(self, "_db_loaded", True)

    @classmethod
    async def get_instance(cls) -> "RecordModel":
        """Get or create the singleton instance and load from DB"""
        instance = cls()
        await instance._load_from_db()
        return instance

    @model_validator(mode="after")
    def auto_save_validator(self):
        if self.__class__.auto_save:
            # Auto-save can't work with async - log warning
            logger.warning(
                f"Auto-save is enabled for {self.__class__.__name__} but update() is now async. Call await instance.update() manually."
            )
        return self

    async def update(self):
        # Get all non-ClassVar fields and their values
        data = {
            field_name: getattr(self, field_name)
            for field_name, field_info in self.model_fields.items()
            if not str(field_info.annotation).startswith("typing.ClassVar")
        }

        await repo_upsert(
            self.__class__.table_name
            if hasattr(self.__class__, "table_name")
            else "record",
            self.record_id,
            data,
        )

        result = await repo_query(
            "SELECT * FROM $record_id", {"record_id": ensure_record_id(self.record_id)}
        )
        if result:
            for key, value in result[0].items():
                if hasattr(self, key):
                    object.__setattr__(
                        self, key, value
                    )  # Use object.__setattr__ to avoid triggering validation again

        return self

    @classmethod
    def clear_instance(cls):
        """Clear the singleton instance (useful for testing)"""
        if cls.record_id in cls._instances:
            del cls._instances[cls.record_id]

    async def patch(self, model_dict: dict):
        """Update model attributes from dictionary and save"""
        for key, value in model_dict.items():
            setattr(self, key, value)
        await self.update()
