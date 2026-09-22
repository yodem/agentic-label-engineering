origin: orchestkit/skills/design-system-tokens/references/w3c-token-spec.md@9.8.0

# W3C Design Token Community Group Specification

## Overview

The W3C Design Token Community Group (DTCG) defines a standard file format for design tokens, enabling interoperability between design tools, development platforms, and token management systems. The specification is available at [design-tokens.github.io/community-group/format/](https://design-tokens.github.io/community-group/format/).

## File Format

Token files use JSON with the `.tokens.json` extension. Each token is an object with `$`-prefixed properties:

| Property | Required | Description |
|----------|----------|-------------|
| `$value` | Yes | The token's resolved value |
| `$type` | Yes* | Token type (can be inherited from parent group) |
| `$description` | No | Human-readable description |
| `$extensions` | No | Vendor-specific metadata |

*`$type` is required but can be declared on a parent group and inherited by all children.

## Token Types

The specification defines these token types:

### Simple Types
- **`color`** - CSS color value (hex, rgb, oklch, etc.)
- **`dimension`** - Number with unit (`16px`, `1.5rem`)
- **`fontFamily`** - Font name string or array
- **`fontWeight`** - Numeric weight (100-900) or keyword
- **`duration`** - Time value (`200ms`, `0.3s`)
- **`cubicBezier`** - Bezier curve array `[x1, y1, x2, y2]`
- **`number`** - Unitless number

### Composite Types
- **`strokeStyle`** - Border stroke definition
- **`border`** - Composed of color, width, style
- **`transition`** - Composed of duration, delay, timing function
- **`shadow`** - Composed of color, offset, blur, spread
- **`gradient`** - Array of color stops
- **`typography`** - Composed of font family, size, weight, line height, letter spacing

## Token References (Aliases)

Tokens can reference other tokens using curly brace syntax:

```json
{
  "color": {
    "blue": {
      "500": { "$type": "color", "$value": "oklch(0.55 0.18 250)" }
    },
    "primary": { "$type": "color", "$value": "{color.blue.500}" }
  }
}
```

References are resolved by the token processing tool (e.g., Style Dictionary). Circular references are invalid.

## Group Inheritance

A `$type` set on a group applies to all descendant tokens that don't specify their own `$type`:

```json
{
  "spacing": {
    "$type": "dimension",
    "sm": { "$value": "8px" },
    "md": { "$value": "16px" },
    "lg": { "$value": "24px" }
  }
}
```

## Extensions

The `$extensions` property allows vendor-specific metadata:

```json
{
  "color": {
    "primary": {
      "$type": "color",
      "$value": "oklch(0.55 0.18 250)",
      "$extensions": {
        "com.figma": { "variableId": "VariableID:123:456" },
        "com.tokens.deprecated": { "since": "2.0.0", "replacement": "color.brand.primary" }
      }
    }
  }
}
```

## Adoption

Major platforms supporting or converging on the W3C format:
- **Figma** - Variables API exports W3C-compatible tokens
- **Tokens Studio** - Figma plugin with full W3C support
- **Style Dictionary 4.x** - Built-in W3C parser
- **Google Material Design 3** - Aligned with DTCG concepts
- **Microsoft Fluent UI** - Token architecture follows DTCG principles
- **Shopify Polaris** - Uses token hierarchy patterns from DTCG
- **Salesforce Lightning** - Early DTCG contributor

## File Organization

Recommended file structure for a token package:

```
tokens/
├── global/
│   ├── color.tokens.json
│   ├── spacing.tokens.json
│   ├── typography.tokens.json
│   └── elevation.tokens.json
├── alias/
│   ├── light.tokens.json
│   └── dark.tokens.json
├── component/
│   ├── button.tokens.json
│   ├── card.tokens.json
│   └── input.tokens.json
└── $metadata.json
```
