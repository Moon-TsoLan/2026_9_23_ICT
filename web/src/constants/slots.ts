/** 对比主体（S4/S5）的配色：星图的边与环、信息栏矩阵的列头共用同一份，
 *  这样"哪条线是谁的"不需要看图例就能对上。刻意避开节点色（金/冰蓝/银）。 */
export const SLOT_CSS = ['#ff7a59', '#5ad1c8', '#b98cff', '#f2e05c']
export const SLOT_HEX = SLOT_CSS.map((c) => parseInt(c.slice(1), 16))
