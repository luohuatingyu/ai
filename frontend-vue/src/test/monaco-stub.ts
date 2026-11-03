/**
 * Monaco Editor 测试桩：仅提供会话详情页用到的最小 API 面，
 * 让单元测试无需加载 3MB 的真实编辑器。
 */
const create = () => ({
  getValue: () => '',
  setValue: () => {},
  onDidChangeModelContent: () => ({ dispose: () => {} }),
  dispose: () => {},
})

export default {
  editor: {
    defineTheme: () => {},
    create,
  },
}
