# FuncGraph - Specialized Linux ftrace Visualization Tool

![FuncGraph Visualization Example](sample.png)

## Project Overview

FuncGraph is a feature-rich ftrace visualization tool for Linux kernel development, performance analysis, and debugging. Key features include:

1. **ftrace Visualization**: Converts Linux kernel function_graph tracer output into interactive HTML
2. **Source Code Linking**: Click function names to jump to corresponding source code locations
3. **High-Performance Parsing**: Fast Python-based address resolution
4. **Intelligent Filtering**: Multi-dimensional real-time filtering and sorting
5. **Call Stack Folding**: Fold/expand function call blocks to simplify complex call stacks

## Core Features

### 🎯 Filtering System

#### Supported Filter Types
- **CPU Filter**: Regex match for CPU number
- **PID Filter**: Regex match for process ID
- **Process Name Filter**: Regex match for process name
- **Return Value Filter**: Supports numbers, macro names, and `all` option
- **Parameter Filter**: String match for function parameters
- **Duration Filter**: Supports comparison operators and sorting

#### Smart Display
- Auto-detects trace data type
- Shows filter input boxes only when data is available
- Hides filter window when no data

#### Hover Hints
- Mouse hover shows full usage instructions
- Auto-positioning, does not block input
- No need to read docs to use

#### Duration Filtering & Sorting
```javascript
// Basic filtering
>10              // Show >10μs
<5&&>2           // Show 2-5μs
>100||<0.1       // Show outliers

// Sorting
sort:desc        // Sort descending
sort:asc         // Sort ascending

// Combined usage
>10 sort:desc    // Show >10μs, sort descending
<5&&>2 sort:asc  // Show 2-5μs, sort ascending
```

#### Suggestions Dropdown
- Click input box to show candidate list
- Real-time filtering of candidates as you type
- Keyboard navigation (up/down arrows)
- Enter or click to select

#### Prefix Handling Fixes
```javascript
// Duration with prefix is correctly filtered
!145.859 us      // Displayed: 145.859, actual: 245.859
>100 && <200     // Correct display (filter by shown value)
sort:desc        // Correct sorting (by actual value)
```

### 📊 HTML Interactive Features

#### Expand/Collapse
- Click `+` / `-` to expand/collapse a single function
- **Expand All**: Expand all visible lines
- **Collapse All**: Collapse all visible lines
- **Progress Display**: Shows progress percentage during operations

#### Call Stack Folding
- **Fold Icon**: Shows `▶` icon before foldable function entry lines
- **Fold Operation**: Click icon to fold/expand function call blocks
- **Nested Support**: Correctly handles folding of nested function calls
- **State Saving**: Fold state auto-saved to localStorage
- **Icon Switching**: `▶` when folded, `▼` when expanded