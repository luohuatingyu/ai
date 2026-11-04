import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import TemplateCreate from '../../templates/TemplateCreate.vue'

const { apiGet, apiPost, apiPut } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn(), apiPut: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: apiPut, delete: vi.fn(), upload: vi.fn() },
}))

const EXISTING_TEMPLATE = {
  id: 't-1',
  name: 'XGBoost 分类',
  category: '模型训练',
  task_type: 'classification',
  description: '旧描述',
  content: 'train({{epochs}})',
  parameters: [
    { name: 'epochs', type: 'number', label: '训练轮数', required: true, default: 20, options: null, description: '' },
  ],
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
})

/** 填写合法表单 */
async function fillValidForm(wrapper: any) {
  const inputs = wrapper.findAll('input')
  await inputs[0].setValue('新模板') // 名称
  // 分类（第一个 ElSelect）
  await wrapper.findComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', '模型训练')
  await flushPromises()
  const textareas = wrapper.findAll('textarea')
  await textareas[0].setValue('train script') // 脚本内容
}

describe('TemplateCreate', () => {
  it('创建模式：合法提交 POST /templates，参数 JSON 正确解析', async () => {
    apiPost.mockResolvedValue({ id: 't-new' })
    const { wrapper, router } = await mountPage(TemplateCreate, { path: '/templates/create' })
    await fillValidForm(wrapper)

    const textareas = wrapper.findAll('textarea')
    await textareas[1].setValue('[{"name": "epochs", "type": "number", "label": "训练轮数"}]')

    const submitBtn = wrapper.findAll('button').find((b: any) => b.text().includes('创建模板'))
    await submitBtn!.trigger('click')
    await flushPromises()

    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/templates')
    expect(payload.name).toBe('新模板')
    expect(payload.parameters).toEqual([{ name: 'epochs', type: 'number', label: '训练轮数' }])
    expect(payload.dataset_requirements).toEqual({})
    expect(router.currentRoute.value.path).toBe('/templates/library')
  })

  it('参数定义 JSON 非法时阻止提交并内联提示', async () => {
    const { wrapper } = await mountPage(TemplateCreate, { path: '/templates/create' })
    await fillValidForm(wrapper)
    const textareas = wrapper.findAll('textarea')
    await textareas[1].setValue('{invalid}')

    const submitBtn = wrapper.findAll('button').find((b: any) => b.text().includes('创建模板'))
    await submitBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('参数定义必须是 JSON 数组')
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('参数定义缺少 name/type/label 时阻止提交', async () => {
    const { wrapper } = await mountPage(TemplateCreate, { path: '/templates/create' })
    await fillValidForm(wrapper)
    const textareas = wrapper.findAll('textarea')
    await textareas[1].setValue('[{"name": "epochs"}]') // 缺 type/label

    const submitBtn = wrapper.findAll('button').find((b: any) => b.text().includes('创建模板'))
    await submitBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('参数定义必须是 JSON 数组')
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('编辑模式：加载模板详情预填并提交 PUT', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/templates/t-1' ? Promise.resolve(EXISTING_TEMPLATE) : Promise.resolve({})
    )
    apiPut.mockResolvedValue({})
    const { wrapper } = await mountPage(TemplateCreate, { path: '/templates/create?id=t-1' })

    // 预填断言
    const inputs = wrapper.findAll('input')
    expect((inputs[0].element as HTMLInputElement).value).toBe('XGBoost 分类')

    const saveBtn = wrapper.findAll('button').find((b: any) => b.text().includes('保存修改'))
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(apiPut).toHaveBeenCalledWith('/templates/t-1', expect.objectContaining({
      name: 'XGBoost 分类',
      content: 'train({{epochs}})',
      parameters: [expect.objectContaining({ name: 'epochs', default: 20 })],
    }))
    expect(apiPost).not.toHaveBeenCalled()
  })
})
