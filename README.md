# Home Assistant component to support the SPIN SDC 1 remote

This adds support for the [SPIN SDC 1 remote](http://spinremote.com) to [Home Assistant](http://home-assistant.io).
Since this component is in some kind of alpha state, you'll have to add it to your custom_components manually.
The [bluepy](https://github.com/IanHarvey/bluepy) lib (only works on Linux) should be installed automaticly.
The current code has been tested on only one SPIN Remote, what happens if you connect multiple remotes is unknown!

## Installation
1. SSH into your Home Assistant device
2. Make sure Git and libcap are installed: `sudo apt-get install git libcap2-bin`
3. Go into your Home Assistant config directory: `cd /home/homeassistant/.homeassistant`
4. If you don't have a `custom_components` directory create one: `mkdir custom_components`
5. Clone this repo: `git clone https://github.com/BillyNate/home-assistant-component-spin-sdc-1.git custom_components/spin_remote`
6. Add the component to your configuration: `sudo nano configuration.yaml`

        spin_remote:
          - platform: spin_sdc_1

7. Restart Home Assistant: `sudo systemctl restart home-assistant@pi`
8. When the requirements have been installed, set the capabilities: `sudo setcap "cap_net_raw,cap_net_admin+eip" "deps/bluepy/bluepy-helper"`

## Add some action

Currently the following features are supported:
- Change profile ID / Listen to profile ID changes (stored as the state of the entity)
- Override LED color (setting it to [0, 0, 0] will cancel the override)
- Listen to action notifications

Some example confugurations:

### LED coloring when connected

    script:
      blink_spin:
      alias: Blink SPIN Remote in various colors
      sequence:
        - service: spin_remote.rgb_color
          entity_id: spin_remote.spin_1
          data:
            rgb_color: [255, 0, 0]
        - delay:
            seconds: 1
        - service: spin_remote.rgb_color
          entity_id: spin_remote.spin_1
          data:
            rgb_color: [0, 255, 0]
        - delay:
            seconds: 1
        - service: spin_remote.rgb_color
          entity_id: spin_remote.spin_1
          data:
            rgb_color: [0, 0, 255]
        - delay:
            seconds: 1
        - service: spin_remote.rgb_color
          entity_id: spin_remote.spin_1
          data:
            rgb_color: [0, 0, 0]
    
    automation spin_ready:
      alias: Spin ready for use
      trigger:
        platform: state
        entity_id: spin_remote.spin_1
        from: 'connected'
      action:
        service: script.blink_spin

### Adjust brightness of a light

    automation spin_upside_down_clockwise:
      alias: Spin brightness up
      trigger:
        platform: event
        event_type: spin_notification_received
        event_data:
          action: rotate_upside_down_clockwise
          entity_id: spin_remote.spin_1
      condition:
        - condition: state
          entity_id: light.yourlight
          state: 'on'
        - condition: state
          entity_id: spin_remote.spin_1
          state: 'profile_0'
      action:
        - service: light.turn_on
          entity_id: light.yourlight
          data_template:
            transition: 1
            brightness: '{{ states.light.yourlight.attributes.brightness + 25 }}'

## Contributing
1. Check the [issue tracker](https://github.com/BillyNate/home-assistant-component-spin-sdc-1/issues) for open issues, or just come up with a nice new feature
2. Fork it!
3. Create your feature branch: `git checkout -b my-new-feature`
4. Commit your changes: `git commit -m 'Add some feature'`
5. Push to the branch: `git push origin my-new-feature`
6. Submit a pull request :)

## License
[MIT License](LICENSE)