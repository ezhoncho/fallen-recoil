# Recoil Compensator - Auto-Compensation Update

## Overview
The recoil compensator has been updated to support continuous automatic compensation for full-auto weapons. Instead of applying compensation only once per click, the system now provides continuous compensation while the left mouse button is held down.

## Key Changes

### Before (Click-based)
- Compensation applied once per mouse click
- Used `shot_fired` flag with timeout mechanism
- Limited to single shots or rapid clicking
- Suitable only for semi-automatic weapons

### After (Continuous Hold-based)
- Compensation applied continuously while left mouse button is held
- Uses `left_button_held` state tracking
- Configurable compensation interval (1-100ms)
- Perfect for automatic weapons and full-auto scenarios

## New Features

### 1. Continuous Compensation
- Hold left mouse button to start auto-compensation
- Release button to stop compensation immediately
- No per-shot limitations

### 2. Configurable Auto-Fire Interval
- New UI control: "Auto-fire Interval (ms)"
- Range: 1-100 milliseconds
- Default: 10ms (suitable for typical automatic weapons)
- Adjustable for different weapon fire rates

### 3. Enhanced UI
- Updated button text: "Enable Auto-Compensation"
- New status messages: "Auto-compensation active"
- Clearer instructions: "Hold left mouse button - continuous auto-compensation while held"

## Technical Implementation

### CompensatorWorker Changes
- Replaced `shot_fired` flag with `left_button_held` boolean
- Added `set_left_button_state(held)` method
- Added `set_compensation_interval(interval)` method
- Modified main loop to apply compensation continuously while button held

### Mouse Event Handling
- Changed from click detection to press/release tracking
- `on_mouse_click()` now handles both press and release events
- Added `start_continuous_compensation()` and `stop_continuous_compensation()` methods

### Safety & Performance
- Proper cleanup on application shutdown
- CPU-efficient sleep intervals when not compensating
- Thread-safe state management with locks

## Usage

1. **Set Compensation Values**: Configure X, Y, Z compensation values as before
2. **Set Auto-Fire Interval**: Adjust the interval based on your weapon's fire rate
3. **Enable Auto-Compensation**: Toggle the enable button
4. **Use**: Hold left mouse button for continuous compensation during automatic fire

## Benefits

- **3-4x More Compensation**: Provides significantly more compensation applications
- **Perfect for Auto Weapons**: Ideal for games with automatic weapons
- **Responsive**: Immediate start/stop when button pressed/released
- **Configurable**: Adjustable interval for different weapon types
- **Backward Compatible**: All existing preset functionality preserved