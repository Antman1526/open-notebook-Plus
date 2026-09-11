import { beforeEach, describe, expect, it } from 'vitest'

import {
  DEFAULT_MIND_MAP_NOTEBOOK_STATE,
  MIND_MAP_STORAGE_KEY,
  useMindMapStore,
} from './mind-map-store'

describe('mind map store', () => {
  beforeEach(() => {
    localStorage.clear()
    useMindMapStore.setState({ byNotebook: {} })
  })

  it('returns no per-notebook state until one is written', () => {
    expect(useMindMapStore.getState().byNotebook.nb1).toBeUndefined()
  })

  it('persists a per-notebook patch, merged over the defaults', () => {
    useMindMapStore.getState().setState('nb1', { filter: 'source' })
    useMindMapStore.getState().setState('nb1', { query: 'alpha' })

    expect(useMindMapStore.getState().byNotebook.nb1).toEqual({
      ...DEFAULT_MIND_MAP_NOTEBOOK_STATE,
      filter: 'source',
      query: 'alpha',
    })

    const persisted = JSON.parse(localStorage.getItem(MIND_MAP_STORAGE_KEY) ?? '{}')
    expect(persisted.state.byNotebook.nb1).toEqual({
      ...DEFAULT_MIND_MAP_NOTEBOOK_STATE,
      filter: 'source',
      query: 'alpha',
    })
  })

  it('keeps state for different notebooks independent', () => {
    useMindMapStore.getState().setState('nb1', { filter: 'source' })
    useMindMapStore.getState().setState('nb2', { clusterByType: true })

    expect(useMindMapStore.getState().byNotebook.nb1).toMatchObject({ filter: 'source' })
    expect(useMindMapStore.getState().byNotebook.nb2).toMatchObject({ clusterByType: true })
    expect(useMindMapStore.getState().byNotebook.nb1?.clusterByType).toBe(false)
  })

  it('resets a single notebook without touching others', () => {
    useMindMapStore.getState().setState('nb1', { filter: 'note' })
    useMindMapStore.getState().setState('nb2', { filter: 'source' })

    useMindMapStore.getState().reset('nb1')

    expect(useMindMapStore.getState().byNotebook.nb1).toBeUndefined()
    expect(useMindMapStore.getState().byNotebook.nb2).toMatchObject({ filter: 'source' })
  })

  it('fails closed on a malformed persisted filter instead of throwing', async () => {
    localStorage.setItem(
      MIND_MAP_STORAGE_KEY,
      JSON.stringify({
        state: { byNotebook: { nb1: { filter: 'bogus', query: 42, clusterByType: 'yes' } } },
        version: 0,
      }),
    )

    await useMindMapStore.persist.rehydrate()

    expect(useMindMapStore.getState().byNotebook.nb1).toEqual(DEFAULT_MIND_MAP_NOTEBOOK_STATE)
  })

  it('rehydrates a valid persisted notebook state', async () => {
    localStorage.setItem(
      MIND_MAP_STORAGE_KEY,
      JSON.stringify({
        state: { byNotebook: { nb1: { filter: 'studio_artifact', query: 'beta', clusterByType: true } } },
        version: 0,
      }),
    )

    await useMindMapStore.persist.rehydrate()

    expect(useMindMapStore.getState().byNotebook.nb1).toEqual({
      filter: 'studio_artifact',
      query: 'beta',
      clusterByType: true,
    })
  })
})
