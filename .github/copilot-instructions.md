# Project general coding guidelines

## Conversion
- Use Chinese in chat

## Code Style
- Prefer modern Python (3.10+) features like match/case, structural pattern matching, `|` union types
- Use type hints for all function signatures and important variables
- Prefer list/dict/set comprehensions and generator expressions over raw loops
- Use context managers (`with` statement) for resource management
- Prefer `dataclasses` or `NamedTuple` for data containers
- Use f-strings for string formatting instead of `%` or `.format()`

## Naming Conventions
- Use `PascalCase` for class names
- Use `snake_case` for variables, functions, and methods
- Prefix private members with single underscore (`_private`)
- Use `UPPER_CASE` for module-level constants
- Use descriptive type variable names (e.g., `ItemType`, `KeyType` instead of just `T`)

## Code Quality
- Use meaningful variable and function names that clearly describe their purpose
- Include docstrings for all public functions, classes, and modules
- Add error handling using specific exception types (avoid bare `except:`)
- Use `@dataclass(frozen=True)` for immutable value objects
- Use `assert` and `typing.assert_never` for exhaustiveness checks
- Add `-> None` return type annotations explicitly
- Use `collections.abc` protocols (`Sequence`, `Mapping`, `Iterable`) for flexible typing
- Use `typing.Optional[X]` or `X | None` for nullable values

## Project-Specific Patterns
- Use `Protocol` for structural subtyping (duck typing with type safety)
- Use `TypeVar` and `Generic` for reusable generic classes/functions
- Prefer composition over inheritance; use `Protocol` instead of `ABC` where possible
- Use `functools.lru_cache` / `cache` for memoization
- Follow defensive programming: validate inputs early, use `typing.TypeGuard` for narrowing
