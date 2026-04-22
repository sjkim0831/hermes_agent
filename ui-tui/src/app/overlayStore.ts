import { atom, computed } from 'nanostores'

import type { OverlayState } from './interfaces.js'

const buildOverlayState = (): OverlayState => ({
  approval: null,
  authPicker: false,
  clarify: null,
  confirm: null,
  modelPicker: false,
  pager: null,
  picker: false,
  routingPicker: false,
  secret: null,
  skillsHub: false,
  sudo: null
})

export const $overlayState = atom<OverlayState>(buildOverlayState())

export const $isBlocked = computed(
  $overlayState,
  ({ approval, authPicker, clarify, confirm, modelPicker, pager, picker, routingPicker, secret, skillsHub, sudo }) =>
    Boolean(approval || authPicker || clarify || confirm || modelPicker || pager || picker || routingPicker || secret || skillsHub || sudo)
)

export const getOverlayState = () => $overlayState.get()

export const patchOverlayState = (next: Partial<OverlayState> | ((state: OverlayState) => OverlayState)) =>
  $overlayState.set(typeof next === 'function' ? next($overlayState.get()) : { ...$overlayState.get(), ...next })

export const resetOverlayState = () => $overlayState.set(buildOverlayState())
