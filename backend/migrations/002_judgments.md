# 002 复发判定留痕与设备能量判定

配合"新签设备协议按分离设备能量判超标、分不清由主管复核、未判定挡住结案、结案页可查判定依据"的规则，新增 `judgments` 表，并给 `agreements`、`breaches` 各加一列。首次启动由 create_all 建立；已有库需显式执行：

```sql
ALTER TABLE agreements ADD COLUMN energy_basis VARCHAR(20) NOT NULL DEFAULT 'total';
ALTER TABLE breaches ADD COLUMN judgment_id INT NULL,
  ADD CONSTRAINT fk_breaches_judgment FOREIGN KEY (judgment_id) REFERENCES judgments(id);
CREATE TABLE judgments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agreement_id INT NOT NULL REFERENCES agreements(id),
  event_id INT NOT NULL REFERENCES noise_events(id),
  mode VARCHAR(20) NOT NULL,
  total_energy FLOAT NOT NULL,
  equipment_energy FLOAT NULL,
  threshold FLOAT NOT NULL,
  verdict VARCHAR(20) NOT NULL DEFAULT 'pending',
  basis TEXT NOT NULL,
  reviewer VARCHAR(60) NOT NULL DEFAULT '',
  decided_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT one_judgment_per_event UNIQUE (agreement_id, event_id)
);
```

要点：

- `agreements.energy_basis`：`total` 为原约定（总能量判定，说话声抬高总能量也会判复发）；`equipment` 为新签设备协议（按分离出的设备能量判定）。起草时按归因来源自动确定，既有协议迁移后保持 `total`。
- `judgments` 记录每次判定的模式、总能量/设备能量、阈值、结论（`breach` 复发 / `clear` 未超标 / `pending` 待主管复核）与文字依据；同协议同事件唯一，保证补录不重复判定。
- `pending` 判定不创建复发记录、不重开整改，但在主管复核前挡住结案；复核或新成分分离覆盖该事件后补判。
- `breaches.judgment_id` 把复发记录关联回判定依据，可为空（迁移前的旧复发记录无对应判定）。
