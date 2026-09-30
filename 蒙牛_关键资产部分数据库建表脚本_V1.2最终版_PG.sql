-- 蒙牛网点运营平台：关键资产识别模块
-- PostgreSQL 六张正式业务表建表脚本
-- 版本：V1.2 最终版
-- 生成日期：2026-09-24
-- 目标数据库：image_recognition
-- 字符编码：UTF-8；排序规则由目标数据库实例统一配置。
-- 说明：本脚本不包含 DROP TABLE；请在空库或确认无同名对象的 schema 中执行。

SET client_encoding = 'UTF8';
SET search_path TO public;

BEGIN;

-- ============================================================
-- 1. mn_task_list - 关键资产识别任务表
-- ============================================================
CREATE TABLE public.mn_task_list (
    task_id VARCHAR(64) NOT NULL,
    data_source SMALLINT NOT NULL,
    quality_check SMALLINT NOT NULL,
    auto_run SMALLINT NOT NULL,
    business_code VARCHAR(64) NOT NULL,
    business_unit VARCHAR(64) NOT NULL,
    district_code VARCHAR(64),
    district_name VARCHAR(64),
    province_code VARCHAR(64),
    province_name VARCHAR(64),
    capture_date_start DATE,
    capture_date_end DATE,
    task_status SMALLINT NOT NULL,
    sync_date TIMESTAMP NOT NULL,
    batch_no INTEGER NOT NULL,
    remark VARCHAR(500) DEFAULT '',
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    create_by VARCHAR(32) NOT NULL DEFAULT '',
    update_by VARCHAR(32) NOT NULL DEFAULT '',
    deleted SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT pk_mn_task_list PRIMARY KEY (task_id),
    CONSTRAINT ck_task_data_source CHECK (data_source IN (1, 2)),
    CONSTRAINT ck_task_quality_check CHECK (quality_check IN (0, 1)),
    CONSTRAINT ck_task_auto_run CHECK (auto_run IN (0, 1)),
    CONSTRAINT ck_task_capture_date CHECK (capture_date_start IS NULL OR capture_date_end IS NULL OR capture_date_start <= capture_date_end),
    CONSTRAINT ck_task_deleted CHECK (deleted IN (0, 1))
);

COMMENT ON TABLE public.mn_task_list IS '关键资产识别任务表';
COMMENT ON COLUMN public.mn_task_list.task_id IS '主键ID';
COMMENT ON COLUMN public.mn_task_list.data_source IS '数据来源(1-中台照片 2-人工上传)';
COMMENT ON COLUMN public.mn_task_list.quality_check IS '质检开关状态快照(0-关闭 1-启用)，记录质检时开关配置';
COMMENT ON COLUMN public.mn_task_list.auto_run IS '自动增量执行开关(0-关闭 1-启用)';
COMMENT ON COLUMN public.mn_task_list.business_code IS '事业部编码(中台筛选条件 / 人工上传必填归属)';
COMMENT ON COLUMN public.mn_task_list.business_unit IS '事业部(中台筛选条件 / 人工上传必填归属)';
COMMENT ON COLUMN public.mn_task_list.district_code IS '大区编码(中台筛选条件；人工上传未随文件提供为空)';
COMMENT ON COLUMN public.mn_task_list.district_name IS '大区(中台筛选条件；人工上传未随文件提供为空)';
COMMENT ON COLUMN public.mn_task_list.province_code IS '省区编码(中台筛选条件；人工上传未随文件提供为空)';
COMMENT ON COLUMN public.mn_task_list.province_name IS '省区(中台筛选条件；人工上传未随文件提供为空)';
COMMENT ON COLUMN public.mn_task_list.capture_date_start IS '拍摄开始日期(中台照片筛选条件)';
COMMENT ON COLUMN public.mn_task_list.capture_date_end IS '拍摄结束日期(中台照片筛选条件)';
COMMENT ON COLUMN public.mn_task_list.task_status IS '任务状态（0-待执行 1-执行中 2-已完成 3-异常）';
COMMENT ON COLUMN public.mn_task_list.sync_date IS '同步日期（首次为任务创建日期；开启自动增量后为最近执行日期）';
COMMENT ON COLUMN public.mn_task_list.batch_no IS '表示当前是第几次执行。任务可能会重试。';
COMMENT ON COLUMN public.mn_task_list.remark IS '备注';
COMMENT ON COLUMN public.mn_task_list.create_time IS '创建时间';
COMMENT ON COLUMN public.mn_task_list.update_time IS '更新时间';
COMMENT ON COLUMN public.mn_task_list.create_by IS '创建人ID';
COMMENT ON COLUMN public.mn_task_list.update_by IS '更新人ID';
COMMENT ON COLUMN public.mn_task_list.deleted IS '逻辑删除标识(0-未删除 1-已删除)';

-- ============================================================
-- 2. mn_task_photo_list - 任务照片清单表
-- ============================================================
CREATE TABLE public.mn_task_photo_list (
    image_list_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    image_id VARCHAR(64) NOT NULL,
    image_url VARCHAR(512) NOT NULL,
    shop_id VARCHAR(64),
    sync_date TIMESTAMP NOT NULL,
    remark VARCHAR(500) DEFAULT '',
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    create_by VARCHAR(32) NOT NULL DEFAULT '',
    update_by VARCHAR(32) NOT NULL DEFAULT '',
    deleted SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT pk_mn_task_photo_list PRIMARY KEY (image_list_id),
    CONSTRAINT fk_task_photo_task FOREIGN KEY (task_id) REFERENCES public.mn_task_list(task_id),
    CONSTRAINT ck_task_photo_deleted CHECK (deleted IN (0, 1))
);

COMMENT ON TABLE public.mn_task_photo_list IS '任务照片清单表';
COMMENT ON COLUMN public.mn_task_photo_list.image_list_id IS '任务明细ID（主键）';
COMMENT ON COLUMN public.mn_task_photo_list.task_id IS '任务编号';
COMMENT ON COLUMN public.mn_task_photo_list.image_id IS '照片ID';
COMMENT ON COLUMN public.mn_task_photo_list.image_url IS '图片访问地址';
COMMENT ON COLUMN public.mn_task_photo_list.shop_id IS '门店ID';
COMMENT ON COLUMN public.mn_task_photo_list.sync_date IS '同步日期（首次为创建日期；同一任务后续增量照片记录各自执行日期）';
COMMENT ON COLUMN public.mn_task_photo_list.remark IS '备注';
COMMENT ON COLUMN public.mn_task_photo_list.create_time IS '创建时间';
COMMENT ON COLUMN public.mn_task_photo_list.update_time IS '更新时间';
COMMENT ON COLUMN public.mn_task_photo_list.create_by IS '创建人ID';
COMMENT ON COLUMN public.mn_task_photo_list.update_by IS '更新人ID';
COMMENT ON COLUMN public.mn_task_photo_list.deleted IS '逻辑删除标识(0-未删除 1-已删除)';

-- ============================================================
-- 3. mn_image_quality_check - 图片质量检查结果表
-- ============================================================
CREATE TABLE public.mn_image_quality_check (
    quality_check_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    image_list_id VARCHAR(64) NOT NULL,
    image_id VARCHAR(64) NOT NULL,
    image_url VARCHAR(512) NOT NULL,
    scene_type SMALLINT NOT NULL,
    quality_issue VARCHAR(16),
    has_price_tag SMALLINT NOT NULL,
    quality_check_result SMALLINT NOT NULL,
    score NUMERIC(5,2) NOT NULL,
    model_latency INTEGER NOT NULL,
    agent_recognition_result JSONB NOT NULL,
    remark VARCHAR(500) DEFAULT '',
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    create_by VARCHAR(32) NOT NULL DEFAULT '',
    update_by VARCHAR(32) NOT NULL DEFAULT '',
    deleted SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT pk_mn_image_quality_check PRIMARY KEY (quality_check_id),
    CONSTRAINT fk_quality_task FOREIGN KEY (task_id) REFERENCES public.mn_task_list(task_id),
    CONSTRAINT fk_quality_photo FOREIGN KEY (image_list_id) REFERENCES public.mn_task_photo_list(image_list_id),
    CONSTRAINT ck_quality_scene_type CHECK (scene_type IN (0, 1, 2, 3)),
    CONSTRAINT ck_quality_has_price_tag CHECK (has_price_tag IN (0, 1)),
    CONSTRAINT ck_quality_result CHECK (quality_check_result IN (0, 1)),
    CONSTRAINT ck_quality_score CHECK (score >= 0 AND score <= 1.00),
    CONSTRAINT ck_quality_deleted CHECK (deleted IN (0, 1))
);

COMMENT ON TABLE public.mn_image_quality_check IS '图片质量检查结果表';
COMMENT ON COLUMN public.mn_image_quality_check.quality_check_id IS '主键ID';
COMMENT ON COLUMN public.mn_image_quality_check.task_id IS '任务编号';
COMMENT ON COLUMN public.mn_image_quality_check.image_list_id IS '任务明细id';
COMMENT ON COLUMN public.mn_image_quality_check.image_id IS '照片ID';
COMMENT ON COLUMN public.mn_image_quality_check.image_url IS '图片访问地址';
COMMENT ON COLUMN public.mn_image_quality_check.scene_type IS '场景类型(0-其他 1-堆头 2-货架3-冰柜)';
COMMENT ON COLUMN public.mn_image_quality_check.quality_issue IS '质量问题：NULL 表示无问题；1-照片模糊 2-过曝 3-光线不足 4-文件损坏，多个问题逗号拼接，比如1,2,3';
COMMENT ON COLUMN public.mn_image_quality_check.has_price_tag IS '是否有价签(0-否 1-是)';
COMMENT ON COLUMN public.mn_image_quality_check.quality_check_result IS '综合质量结论(0-不合格 1-合格 )';
COMMENT ON COLUMN public.mn_image_quality_check.score IS '置信度(Agent自主评判，百分比0-1.00)';
COMMENT ON COLUMN public.mn_image_quality_check.model_latency IS '模型耗时(毫秒)';
COMMENT ON COLUMN public.mn_image_quality_check.agent_recognition_result IS 'agent识别结果的原始json数据';
COMMENT ON COLUMN public.mn_image_quality_check.remark IS '备注';
COMMENT ON COLUMN public.mn_image_quality_check.create_time IS '创建时间';
COMMENT ON COLUMN public.mn_image_quality_check.update_time IS '更新时间';
COMMENT ON COLUMN public.mn_image_quality_check.create_by IS '创建人ID';
COMMENT ON COLUMN public.mn_image_quality_check.update_by IS '更新人ID';
COMMENT ON COLUMN public.mn_image_quality_check.deleted IS '逻辑删除标识(0-未删除 1-已删除)';

-- ============================================================
-- 4. mn_sku_recognition - SKU识别结果表
-- ============================================================
CREATE TABLE public.mn_sku_recognition (
    sku_recognition_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    image_list_id VARCHAR(64) NOT NULL,
    image_id VARCHAR(64) NOT NULL,
    image_url VARCHAR(512) NOT NULL,
    shop_id VARCHAR(64),
    agent_recognition_result JSONB NOT NULL,
    sku_parse_json JSONB NOT NULL,
    recognition_time TIMESTAMP NOT NULL,
    model_latency INTEGER NOT NULL,
    remark VARCHAR(500),
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    create_by VARCHAR(32) NOT NULL DEFAULT '',
    update_by VARCHAR(32) NOT NULL DEFAULT '',
    deleted SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT pk_mn_sku_recognition PRIMARY KEY (sku_recognition_id),
    CONSTRAINT fk_sku_task FOREIGN KEY (task_id) REFERENCES public.mn_task_list(task_id),
    CONSTRAINT fk_sku_photo FOREIGN KEY (image_list_id) REFERENCES public.mn_task_photo_list(image_list_id),
    CONSTRAINT ck_sku_deleted CHECK (deleted IN (0, 1))
);

COMMENT ON TABLE public.mn_sku_recognition IS 'SKU识别结果表';
COMMENT ON COLUMN public.mn_sku_recognition.sku_recognition_id IS '主键ID';
COMMENT ON COLUMN public.mn_sku_recognition.task_id IS '任务编号';
COMMENT ON COLUMN public.mn_sku_recognition.image_list_id IS '任务明细id';
COMMENT ON COLUMN public.mn_sku_recognition.image_id IS '照片ID(唯一标识，中台或上传系统的图片标识)';
COMMENT ON COLUMN public.mn_sku_recognition.image_url IS '图片访问地址';
COMMENT ON COLUMN public.mn_sku_recognition.shop_id IS '门店ID';
COMMENT ON COLUMN public.mn_sku_recognition.agent_recognition_result IS 'agent识别结果的原始json数据';
COMMENT ON COLUMN public.mn_sku_recognition.sku_parse_json IS '解析结果JSON（含 bbox、skuid、skuname、score、company、brand 等字段）';
COMMENT ON COLUMN public.mn_sku_recognition.recognition_time IS '识别时间';
COMMENT ON COLUMN public.mn_sku_recognition.model_latency IS '模型耗时(毫秒)';
COMMENT ON COLUMN public.mn_sku_recognition.remark IS '备注';
COMMENT ON COLUMN public.mn_sku_recognition.create_time IS '创建时间';
COMMENT ON COLUMN public.mn_sku_recognition.update_time IS '更新时间';
COMMENT ON COLUMN public.mn_sku_recognition.create_by IS '创建人ID';
COMMENT ON COLUMN public.mn_sku_recognition.update_by IS '更新人ID';
COMMENT ON COLUMN public.mn_sku_recognition.deleted IS '逻辑删除标识(0-未删除 1-已删除)';

-- ============================================================
-- 5. mn_price_tag_recognition - 价签识别结果表
-- ============================================================
CREATE TABLE public.mn_price_tag_recognition (
    price_tag_recognition_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    image_list_id VARCHAR(64) NOT NULL,
    image_id VARCHAR(64) NOT NULL,
    image_url VARCHAR(512) NOT NULL,
    shop_id VARCHAR(64),
    agent_recognition_result JSONB NOT NULL,
    price_parse_json JSONB NOT NULL,
    model_latency INTEGER NOT NULL,
    recognition_time TIMESTAMP NOT NULL,
    remark VARCHAR(500) DEFAULT '',
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    create_by VARCHAR(32) NOT NULL DEFAULT '',
    update_by VARCHAR(32) NOT NULL DEFAULT '',
    deleted SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT pk_mn_price_tag_recognition PRIMARY KEY (price_tag_recognition_id),
    CONSTRAINT fk_price_tag_task FOREIGN KEY (task_id) REFERENCES public.mn_task_list(task_id),
    CONSTRAINT fk_price_tag_photo FOREIGN KEY (image_list_id) REFERENCES public.mn_task_photo_list(image_list_id),
    CONSTRAINT ck_price_tag_deleted CHECK (deleted IN (0, 1))
);

COMMENT ON TABLE public.mn_price_tag_recognition IS '价签识别结果表';
COMMENT ON COLUMN public.mn_price_tag_recognition.price_tag_recognition_id IS '主键ID';
COMMENT ON COLUMN public.mn_price_tag_recognition.task_id IS '任务编号';
COMMENT ON COLUMN public.mn_price_tag_recognition.image_list_id IS '任务明细id';
COMMENT ON COLUMN public.mn_price_tag_recognition.image_id IS '照片ID(唯一标识，中台或上传系统的图片标识)';
COMMENT ON COLUMN public.mn_price_tag_recognition.image_url IS '图片访问地址';
COMMENT ON COLUMN public.mn_price_tag_recognition.shop_id IS '门店ID';
COMMENT ON COLUMN public.mn_price_tag_recognition.agent_recognition_result IS 'agent识别结果的原始json数据';
COMMENT ON COLUMN public.mn_price_tag_recognition.price_parse_json IS '解析结果JSON（含 bbox、skuid、skuname、score、company、brand 等字段）';
COMMENT ON COLUMN public.mn_price_tag_recognition.model_latency IS '模型耗时(毫秒)';
COMMENT ON COLUMN public.mn_price_tag_recognition.recognition_time IS '识别时间';
COMMENT ON COLUMN public.mn_price_tag_recognition.remark IS '备注';
COMMENT ON COLUMN public.mn_price_tag_recognition.create_time IS '创建时间';
COMMENT ON COLUMN public.mn_price_tag_recognition.update_time IS '更新时间';
COMMENT ON COLUMN public.mn_price_tag_recognition.create_by IS '创建人ID';
COMMENT ON COLUMN public.mn_price_tag_recognition.update_by IS '更新人ID';
COMMENT ON COLUMN public.mn_price_tag_recognition.deleted IS '逻辑删除标识(0-未删除 1-已删除)';

-- ============================================================
-- 6. mn_image_recognition_result - 图片识别业务结果表
-- ============================================================
CREATE TABLE public.mn_image_recognition_result (
    image_recognition_result_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    image_list_id VARCHAR(64) NOT NULL,
    image_id VARCHAR(64) NOT NULL,
    image_url VARCHAR(512) NOT NULL,
    sku_code VARCHAR(64) NOT NULL,
    sku_name VARCHAR(255) NOT NULL,
    price NUMERIC(18,2) NOT NULL,
    company VARCHAR(255),
    brand VARCHAR(255),
    sku_min_price NUMERIC(18,2) NOT NULL,
    sku_max_price NUMERIC(18,2) NOT NULL,
    price_check_result SMALLINT NOT NULL,
    original_sku_bbox JSONB,
    original_price_bbox JSONB,
    remark VARCHAR(500),
    create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    create_by VARCHAR(32) NOT NULL DEFAULT '',
    update_by VARCHAR(32) NOT NULL DEFAULT '',
    deleted SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT pk_mn_image_recognition_result PRIMARY KEY (image_recognition_result_id),
    CONSTRAINT fk_result_task FOREIGN KEY (task_id) REFERENCES public.mn_task_list(task_id),
    CONSTRAINT fk_result_photo FOREIGN KEY (image_list_id) REFERENCES public.mn_task_photo_list(image_list_id),
    CONSTRAINT ck_result_price CHECK (price >= 0),
    CONSTRAINT ck_result_price_range CHECK (sku_min_price >= 0 AND sku_max_price >= 0 AND sku_min_price <= sku_max_price),
    CONSTRAINT ck_result_deleted CHECK (deleted IN (0, 1))
);

COMMENT ON TABLE public.mn_image_recognition_result IS '图片识别业务结果表';
COMMENT ON COLUMN public.mn_image_recognition_result.image_recognition_result_id IS '主键ID';
COMMENT ON COLUMN public.mn_image_recognition_result.task_id IS '任务编号';
COMMENT ON COLUMN public.mn_image_recognition_result.image_list_id IS '任务明细id';
COMMENT ON COLUMN public.mn_image_recognition_result.image_id IS '照片ID(唯一标识，中台或上传系统的图片标识)';
COMMENT ON COLUMN public.mn_image_recognition_result.image_url IS '图片访问地址';
COMMENT ON COLUMN public.mn_image_recognition_result.sku_code IS 'SKU编码(识别结果关联的商品编码)';
COMMENT ON COLUMN public.mn_image_recognition_result.sku_name IS '商品名称';
COMMENT ON COLUMN public.mn_image_recognition_result.price IS '价格';
COMMENT ON COLUMN public.mn_image_recognition_result.company IS '公司';
COMMENT ON COLUMN public.mn_image_recognition_result.brand IS '品牌';
COMMENT ON COLUMN public.mn_image_recognition_result.sku_min_price IS 'sku最低价';
COMMENT ON COLUMN public.mn_image_recognition_result.sku_max_price IS 'sku最高价';
COMMENT ON COLUMN public.mn_image_recognition_result.price_check_result IS '价格校验结果';
COMMENT ON COLUMN public.mn_image_recognition_result.original_sku_bbox IS '价签匹配原始SKU对应的BBOX';
COMMENT ON COLUMN public.mn_image_recognition_result.original_price_bbox IS '价签匹配原始价格对应的BBOX';
COMMENT ON COLUMN public.mn_image_recognition_result.remark IS '备注';
COMMENT ON COLUMN public.mn_image_recognition_result.create_time IS '创建时间';
COMMENT ON COLUMN public.mn_image_recognition_result.update_time IS '更新时间';
COMMENT ON COLUMN public.mn_image_recognition_result.create_by IS '创建人ID';
COMMENT ON COLUMN public.mn_image_recognition_result.update_by IS '更新人ID';
COMMENT ON COLUMN public.mn_image_recognition_result.deleted IS '逻辑删除标识(0-未删除 1-已删除)';

-- ============================================================
-- 索引
-- ============================================================
-- mn_task_list
CREATE INDEX idx_task_business_code ON public.mn_task_list (business_code) WHERE deleted = 0;
CREATE INDEX idx_task_create_by ON public.mn_task_list (create_by) WHERE deleted = 0;
CREATE INDEX idx_task_auto_sync ON public.mn_task_list (auto_run, sync_date) WHERE deleted = 0;

-- mn_task_photo_list
CREATE INDEX idx_task_photo_task_id ON public.mn_task_photo_list (task_id) WHERE deleted = 0;
CREATE INDEX idx_task_photo_image_id ON public.mn_task_photo_list (image_id) WHERE deleted = 0;
CREATE INDEX idx_task_photo_task_sync ON public.mn_task_photo_list (task_id, sync_date) WHERE deleted = 0;

-- mn_image_quality_check
CREATE INDEX idx_quality_task_id ON public.mn_image_quality_check (task_id) WHERE deleted = 0;
CREATE INDEX idx_quality_image_list_id ON public.mn_image_quality_check (image_list_id) WHERE deleted = 0;

-- mn_sku_recognition
CREATE INDEX idx_sku_task_id ON public.mn_sku_recognition (task_id) WHERE deleted = 0;
CREATE INDEX idx_sku_image_list_id ON public.mn_sku_recognition (image_list_id) WHERE deleted = 0;
CREATE INDEX idx_sku_image_id ON public.mn_sku_recognition (image_id) WHERE deleted = 0;

-- mn_price_tag_recognition
CREATE INDEX idx_price_tag_task_id ON public.mn_price_tag_recognition (task_id) WHERE deleted = 0;
CREATE INDEX idx_price_tag_image_list_id ON public.mn_price_tag_recognition (image_list_id) WHERE deleted = 0;
CREATE INDEX idx_price_tag_image_id ON public.mn_price_tag_recognition (image_id) WHERE deleted = 0;

-- mn_image_recognition_result
CREATE INDEX idx_result_task_id ON public.mn_image_recognition_result (task_id) WHERE deleted = 0;
CREATE INDEX idx_result_image_list_id ON public.mn_image_recognition_result (image_list_id) WHERE deleted = 0;
CREATE INDEX idx_result_image_id ON public.mn_image_recognition_result (image_id) WHERE deleted = 0;
CREATE INDEX idx_result_sku_code ON public.mn_image_recognition_result (sku_code) WHERE deleted = 0;

-- ============================================================
-- update_time 自动更新时间触发器
-- PostgreSQL 不支持 MySQL 的 ON UPDATE CURRENT_TIMESTAMP 列语法。
-- ============================================================
CREATE OR REPLACE FUNCTION public.set_update_time()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.update_time = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_mn_task_list_update_time
BEFORE UPDATE ON public.mn_task_list
FOR EACH ROW
EXECUTE FUNCTION public.set_update_time();

CREATE TRIGGER trg_mn_task_photo_list_update_time
BEFORE UPDATE ON public.mn_task_photo_list
FOR EACH ROW
EXECUTE FUNCTION public.set_update_time();

CREATE TRIGGER trg_mn_image_quality_check_update_time
BEFORE UPDATE ON public.mn_image_quality_check
FOR EACH ROW
EXECUTE FUNCTION public.set_update_time();

CREATE TRIGGER trg_mn_sku_recognition_update_time
BEFORE UPDATE ON public.mn_sku_recognition
FOR EACH ROW
EXECUTE FUNCTION public.set_update_time();

CREATE TRIGGER trg_mn_price_tag_recognition_update_time
BEFORE UPDATE ON public.mn_price_tag_recognition
FOR EACH ROW
EXECUTE FUNCTION public.set_update_time();

CREATE TRIGGER trg_mn_image_recognition_result_update_time
BEFORE UPDATE ON public.mn_image_recognition_result
FOR EACH ROW
EXECUTE FUNCTION public.set_update_time();

COMMIT;

-- 执行后可使用以下语句核对六张表：
-- SELECT tablename FROM pg_tables
-- WHERE schemaname = 'public' AND tablename LIKE 'mn_%'
-- ORDER BY tablename;
