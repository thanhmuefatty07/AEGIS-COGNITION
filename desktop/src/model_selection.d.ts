interface IdentifiedModel {
  readonly connection_id: string;
  readonly model_id: string;
}

export declare function findModelForConnectionAndId<T extends IdentifiedModel>(
  models: readonly T[],
  connectionId: string | null | undefined,
  modelId: string | null | undefined,
): T | undefined;
