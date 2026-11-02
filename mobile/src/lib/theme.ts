import { useColorScheme } from 'react-native';

// Status colours are reserved for state and always shown with a text label.
export const StatusColor = {
  normal: '#0ca30c',
  warning: '#fab219',
  replace: '#d03b3b',
  no_data: '#8a8a85',
} as const;

export const StatusLabel = {
  normal: 'Healthy',
  warning: 'Watch',
  replace: 'Service needed',
  no_data: 'Waiting for data',
} as const;

const palettes = {
  light: {
    text: '#0b0b0b',
    textSecondary: '#52514e',
    background: '#f4f6f9',
    card: '#ffffff',
    border: '#e1e5ec',
    track: '#e8ebf0',
    accent: '#2a78d6',
    accentText: '#ffffff',
  },
  dark: {
    text: '#f0efec',
    textSecondary: '#c3c2b7',
    background: '#0f1218',
    card: '#1a1d24',
    border: '#2a2f39',
    track: '#2a2f39',
    accent: '#3987e5',
    accentText: '#ffffff',
  },
};

export type Palette = typeof palettes.light;

export function usePalette(): Palette {
  return useColorScheme() === 'dark' ? palettes.dark : palettes.light;
}
