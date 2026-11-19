const ORDER = ["Обычный", "Необычный", "Редкий", "Эпический", "Легендарный", "Мифический"];
export const rarClass = (r: string) => `rar-${Math.max(0, ORDER.indexOf(r))}`;

export type Item = { id: number; name: string; emoji: string; rarity: string; price: number };
export type InvItem = { id: number; itemId: number; name: string; emoji: string; rarity: string; price: number };
